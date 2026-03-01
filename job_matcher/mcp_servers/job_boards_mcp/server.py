"""
Job Boards MCP Server
──────────────────────
Exposes job-search tools that scan Indeed, Glassdoor and LinkedIn Jobs.

Tools exposed:
  • search_indeed         — search Indeed job listings
  • search_glassdoor      — search Glassdoor job listings
  • search_linkedin_jobs  — search LinkedIn job listings
  • get_job_details       — fetch full job description by URL

Production backends (configure via env vars):
  - RAPIDAPI_KEY → RapidAPI "JSearch" API (covers Indeed + LinkedIn + Glassdoor)
  - SERPAPI_KEY  → SerpAPI Google Jobs endpoint
  - Tavily       → fallback web search
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    JOB_BOARDS_MCP_PORT,
    RAPIDAPI_KEY,
    SERPAPI_KEY,
    TAVILY_API_KEY,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "search_indeed",
        "description": "Search Indeed for open job listings matching the given criteria.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Job title or keywords, e.g. 'Senior Python Engineer'"},
                "location": {"type": "string", "description": "City, state or 'remote'", "default": ""},
                "max_results": {"type": "integer", "default": 10},
                "remote_only": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_glassdoor",
        "description": "Search Glassdoor for open job listings with salary data when available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "location": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_linkedin_jobs",
        "description": "Search LinkedIn Jobs for open positions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "location": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 10},
                "experience_level": {
                    "type": "string",
                    "enum": ["entry", "mid", "senior", "lead", "executive"],
                    "default": "mid",
                },
                "remote_only": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_arbeitnow",
        "description": "Search Arbeitnow for remote/international job listings (free, no API key required).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_remoteok",
        "description": "Search RemoteOK for remote job listings (free, no API key required).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_job_details",
        "description": "Fetch the full job description from a job listing URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Direct URL to the job posting"},
                "source": {"type": "string", "description": "Source board: indeed | glassdoor | linkedin"},
            },
            "required": ["url"],
        },
    },
]


# ---------------------------------------------------------------------------
# JSearch (RapidAPI) backend — covers Indeed + LinkedIn + Glassdoor
# ---------------------------------------------------------------------------

async def _jsearch(query: str, location: str, max_results: int, **filters) -> list[dict]:
    """
    Call the JSearch API on RapidAPI (supports Indeed, LinkedIn, Glassdoor data).
    Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
    """
    if not RAPIDAPI_KEY:
        return []

    params = {
        "query": f"{query} in {location}" if location else query,
        "page": "1",
        "num_pages": "1",
    }
    if filters.get("remote_only"):
        params["remote_jobs_only"] = "true"

    async with httpx.AsyncClient(timeout=15) as http:
        try:
            resp = await http.get(
                "https://jsearch.p.rapidapi.com/search",
                params=params,
                headers={
                    "X-RapidAPI-Key": RAPIDAPI_KEY,
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("JSearch API error: %s", exc)
            return []

    jobs = []
    for item in data.get("data", [])[:max_results]:
        jobs.append({
            "job_id": item.get("job_id", str(uuid.uuid4())),
            "title": item.get("job_title", ""),
            "company": item.get("employer_name", ""),
            "location": item.get("job_city", "") + ", " + item.get("job_country", ""),
            "work_mode": "remote" if item.get("job_is_remote") else "on_site",
            "job_type": item.get("job_employment_type", "full_time").lower(),
            "salary_min": item.get("job_min_salary"),
            "salary_max": item.get("job_max_salary"),
            "description": item.get("job_description", "")[:2000],
            "required_skills": item.get("job_required_skills") or [],
            "apply_url": item.get("job_apply_link", ""),
            "posted_date": item.get("job_posted_at_datetime_utc", ""),
            "source": item.get("job_publisher", "jsearch").lower(),
        })
    return jobs


async def _tavily_job_search(query: str, location: str, source: str, max_results: int) -> list[dict]:
    """Fallback: use Tavily web search to find job listings."""
    if not TAVILY_API_KEY:
        return []

    # Broad query — site: filters often return 0 results on the free tier
    search_q = f"{query} job openings hiring"
    if location:
        search_q += f" {location}"

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": search_q, "max_results": max_results},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Tavily job search error: %s", exc)
        return []

    jobs = []
    for result in data.get("results", []):
        jobs.append({
            "job_id": str(uuid.uuid4()),
            "title": result.get("title", ""),
            "company": "",
            "location": location,
            "work_mode": None,
            "job_type": "full_time",
            "salary_min": None,
            "salary_max": None,
            "description": result.get("content", "")[:2000],
            "required_skills": [],
            "apply_url": result.get("url", ""),
            "posted_date": "",
            "source": source,
        })
    return jobs


async def _arbeitnow_search(query: str, max_results: int) -> list[dict]:
    """
    Arbeitnow public API — completely free, no API key required.
    Covers thousands of remote/international tech jobs.
    https://www.arbeitnow.com/api/job-board-api
    """
    try:
        params = {"search": query, "page": 1}
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "JobMatcherBot/1.0"}) as http:
            resp = await http.get("https://www.arbeitnow.com/api/job-board-api", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Arbeitnow search error: %s", exc)
        return []

    jobs = []
    for item in data.get("data", [])[:max_results]:
        jobs.append({
            "job_id": str(uuid.uuid4()),
            "title": item.get("title", ""),
            "company": item.get("company_name", ""),
            "location": item.get("location", "Remote"),
            "work_mode": "remote" if item.get("remote") else "on_site",
            "job_type": (item.get("job_types") or ["full_time"])[0].replace("-", "_"),
            "salary_min": None,
            "salary_max": None,
            "description": item.get("description", "")[:2000],
            "required_skills": item.get("tags", []),
            "apply_url": item.get("url", ""),
            "posted_date": str(item.get("created_at", "")),
            "source": "arbeitnow",
        })
    return jobs


async def _remoteok_search(query: str, max_results: int) -> list[dict]:
    """
    RemoteOK public API — completely free, no API key required.
    https://remoteok.com/api
    """
    try:
        tag = query.split()[0].lower()  # RemoteOK searches by tag
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "JobMatcherBot/1.0"}) as http:
            resp = await http.get(f"https://remoteok.com/api?tag={tag}")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("RemoteOK search error: %s", exc)
        return []

    jobs = []
    # First item is a legal/meta notice — skip it
    for item in data[1:max_results + 1]:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        jobs.append({
            "job_id": str(uuid.uuid4()),
            "title": item.get("position", ""),
            "company": item.get("company", ""),
            "location": item.get("location", "Remote"),
            "work_mode": "remote",
            "job_type": "full_time",
            "salary_min": None,
            "salary_max": None,
            "description": item.get("description", "")[:2000],
            "required_skills": item.get("tags", []),
            "apply_url": item.get("url", "") or item.get("apply_url", ""),
            "posted_date": item.get("date", ""),
            "source": "remoteok",
        })
    return jobs


def _mock_jobs(query: str, location: str, source: str, n: int) -> list[dict]:
    """Return mock data when no API keys are present (dev / test mode)."""
    return [
        {
            "job_id": str(uuid.uuid4()),
            "title": f"{query} (Mock)",
            "company": f"Example Corp {i + 1}",
            "location": location or "Remote",
            "work_mode": "remote",
            "job_type": "full_time",
            "salary_min": 100000 + i * 10000,
            "salary_max": 150000 + i * 10000,
            "description": f"This is a mock job listing for {query}. In production this would be a real description.",
            "required_skills": ["Python", "FastAPI", "Docker"],
            "apply_url": f"https://{source}.com/job/{i}",
            "posted_date": "2025-01-01",
            "source": source,
        }
        for i in range(min(n, 3))
    ]


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

async def search_indeed(query: str, location: str = "", max_results: int = 10, remote_only: bool = False) -> dict:
    jobs = await _jsearch(query, location, max_results, remote_only=remote_only)
    if not jobs:
        jobs = await _tavily_job_search(query, location, "indeed", max_results)
    if not jobs:
        jobs = await _arbeitnow_search(query, max_results)
    return {"jobs": jobs, "source": "indeed", "total": len(jobs)}


async def search_glassdoor(query: str, location: str = "", max_results: int = 10) -> dict:
    jobs = await _jsearch(query, location, max_results)
    if not jobs:
        jobs = await _tavily_job_search(query, location, "glassdoor", max_results)
    if not jobs:
        jobs = await _remoteok_search(query, max_results)
    return {"jobs": jobs, "source": "glassdoor", "total": len(jobs)}


async def search_linkedin_jobs(
    query: str, location: str = "", max_results: int = 10,
    experience_level: str = "mid", remote_only: bool = False
) -> dict:
    jobs = await _jsearch(query, location, max_results, remote_only=remote_only)
    if not jobs:
        jobs = await _tavily_job_search(query, location, "linkedin", max_results)
    if not jobs:
        jobs = await _arbeitnow_search(query, max_results)
    return {"jobs": jobs, "source": "linkedin", "total": len(jobs)}


async def search_arbeitnow(query: str, max_results: int = 10) -> dict:
    """Free job search via Arbeitnow — no API key required."""
    jobs = await _arbeitnow_search(query, max_results)
    return {"jobs": jobs, "source": "arbeitnow", "total": len(jobs)}


async def search_remoteok(query: str, max_results: int = 10) -> dict:
    """Free remote job search via RemoteOK — no API key required."""
    jobs = await _remoteok_search(query, max_results)
    return {"jobs": jobs, "source": "remoteok", "total": len(jobs)}


async def get_job_details(url: str, source: str = "") -> dict:
    if not TAVILY_API_KEY:
        return {"url": url, "description": "Full job details not available without Tavily API key.", "source": source}

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(
            "https://api.tavily.com/extract",
            json={"api_key": TAVILY_API_KEY, "urls": [url]},
        )
        data = resp.json()

    raw = data.get("results", [{}])[0].get("raw_content", "")
    return {"url": url, "description": raw[:4000], "source": source}


# ---------------------------------------------------------------------------
# MCP HTTP Server
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "search_indeed": search_indeed,
    "search_glassdoor": search_glassdoor,
    "search_linkedin_jobs": search_linkedin_jobs,
    "search_arbeitnow": search_arbeitnow,
    "search_remoteok": search_remoteok,
    "get_job_details": get_job_details,
}

app = FastAPI(title="Job Boards MCP Server")


@app.get("/health")
async def health():
    return {"status": "ok", "server": "job_boards_mcp"}


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
                "serverInfo": {"name": "job_boards_mcp", "version": "1.0.0"},
            },
        })

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": TOOLS}})

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
                "result": {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False},
            })
        except Exception as exc:
            logger.exception("Tool '%s' raised", tool_name)
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            })

    return JSONResponse({
        "jsonrpc": "2.0", "id": rpc_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=JOB_BOARDS_MCP_PORT, log_level="info")
