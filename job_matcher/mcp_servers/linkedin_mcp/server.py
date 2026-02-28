"""
LinkedIn MCP Server
───────────────────
Exposes tools for fetching and parsing LinkedIn profile data.

MCP transport: HTTP/SSE (streamable-HTTP mode).
Tool list exposed to agents:
  • fetch_linkedin_profile   — scrape a public LinkedIn URL
  • parse_profile_text       — parse free-text / exported PDF profile
  • search_linkedin_people   — keyword search for professionals

In production replace the stub scrapers with:
  - Apify LinkedIn scraper  (https://apify.com/bebity/linkedin-profile-scraper)
  - RapidAPI LinkedIn       (https://rapidapi.com/rockapis-rockapis-default/api/linkedin-data-api)
  - Proxycurl               (https://nubela.co/proxycurl)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from anthropic import Anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from job_matcher.shared.config import ANTHROPIC_API_KEY, DEFAULT_MODEL, LINKEDIN_MCP_PORT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Tool definitions (JSON Schema)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "fetch_linkedin_profile",
        "description": (
            "Fetch and parse a candidate's public LinkedIn profile given its URL. "
            "Returns structured JSON with name, headline, skills, experience, education."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "linkedin_url": {
                    "type": "string",
                    "description": "Full LinkedIn profile URL, e.g. https://www.linkedin.com/in/johndoe",
                },
                "include_contact": {
                    "type": "boolean",
                    "description": "Whether to attempt to extract email/phone (may not be available publicly).",
                    "default": False,
                },
            },
            "required": ["linkedin_url"],
        },
    },
    {
        "name": "parse_profile_text",
        "description": (
            "Parse raw text extracted from a LinkedIn PDF export or copy-pasted profile "
            "and return the same structured JSON as fetch_linkedin_profile."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Raw text content of the LinkedIn profile.",
                }
            },
            "required": ["text"],
        },
    },
    {
        "name": "search_linkedin_people",
        "description": "Search LinkedIn for professionals matching a query and return a list of profile summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords, e.g. 'senior ML engineer San Francisco'"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

client = Anthropic(api_key=ANTHROPIC_API_KEY)


async def _llm_parse(prompt: str) -> dict:
    """Use Claude to extract structured data from free-form text."""
    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
    # Strip markdown fences if present
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
    return json.loads(raw)


async def fetch_linkedin_profile(linkedin_url: str, include_contact: bool = False) -> dict:
    """
    Stub: in production call Proxycurl / Apify.
    Here we use Tavily to fetch HTML then Claude to extract structure.
    """
    tavily_key = os.getenv("TAVILY_API_KEY", "")
    raw_text = ""

    if tavily_key:
        async with httpx.AsyncClient(timeout=20) as http:
            try:
                resp = await http.post(
                    "https://api.tavily.com/extract",
                    json={"api_key": tavily_key, "urls": [linkedin_url]},
                )
                data = resp.json()
                raw_text = data.get("results", [{}])[0].get("raw_content", "")
            except Exception as exc:
                logger.warning("Tavily extract failed: %s", exc)

    if not raw_text:
        raw_text = f"[LinkedIn profile at {linkedin_url} — content not fetched in this environment]"

    return await _llm_parse(
        f"""Extract a structured LinkedIn profile from the text below.
Return ONLY valid JSON matching this schema:
{{
  "full_name": "string",
  "headline": "string",
  "location": "string",
  "summary": "string",
  "skills": ["skill1", "skill2"],
  "experience": [
    {{"company":"","title":"","start_date":"","end_date":null,"description":"","skills_used":[],"achievements":[]}}
  ],
  "education": [
    {{"institution":"","degree":"","field_of_study":"","end_year":null}}
  ],
  "certifications": [],
  "languages": [],
  "years_of_experience": 0
}}

LinkedIn profile text:
{raw_text[:6000]}
"""
    )


async def parse_profile_text(text: str) -> dict:
    return await _llm_parse(
        f"""Extract a structured candidate profile from the raw text below.
Return ONLY valid JSON with fields: full_name, headline, location, summary, skills (list),
experience (list with company/title/start_date/end_date/description/skills_used/achievements),
education (list), certifications (list), languages (list), years_of_experience (float).

Raw text:
{text[:6000]}
"""
    )


async def search_linkedin_people(query: str, max_results: int = 10) -> dict:
    """Stub: returns mocked results. Production → LinkedIn API or Proxycurl."""
    return {
        "results": [
            {
                "name": f"Professional matching '{query}'",
                "headline": "Example headline",
                "linkedin_url": "https://linkedin.com/in/example",
                "location": "San Francisco, CA",
            }
        ],
        "note": "Production implementation requires LinkedIn API / Proxycurl.",
    }


# ---------------------------------------------------------------------------
# MCP HTTP Server (Streamable HTTP transport)
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "fetch_linkedin_profile": fetch_linkedin_profile,
    "parse_profile_text": parse_profile_text,
    "search_linkedin_people": search_linkedin_people,
}

app = FastAPI(title="LinkedIn MCP Server")


@app.get("/health")
async def health():
    return {"status": "ok", "server": "linkedin_mcp"}


# MCP initialize
@app.post("/")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method", "")
    rpc_id = body.get("id")

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "linkedin_mcp", "version": "1.0.0"},
            },
        })

    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {"tools": TOOLS},
        })

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        fn = TOOL_MAP.get(tool_name)
        if fn is None:
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
            })
        try:
            result = await fn(**arguments)
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "isError": False,
                },
            })
        except Exception as exc:
            logger.exception("Tool '%s' raised", tool_name)
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            })

    return JSONResponse({
        "jsonrpc": "2.0", "id": rpc_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=LINKEDIN_MCP_PORT, log_level="info")
