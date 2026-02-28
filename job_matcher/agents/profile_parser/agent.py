"""
Profile Parser Agent
─────────────────────
Skills:
  • parse_linkedin_profile  — given a LinkedIn URL, fetch + parse via LinkedIn MCP
  • parse_resume_text        — given raw resume text, extract structured profile
  • parse_resume_pdf         — given base64 PDF, extract text via Document MCP then parse

This agent owns all profile ingestion logic.
Other agents call it via A2A to obtain a CandidateProfile object.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

import httpx
from anthropic import Anthropic

from job_matcher.a2a.protocol import AgentCard, AgentCapabilities, AgentSkill, Task
from job_matcher.a2a.server import BaseA2AAgent
from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    MAX_TOKENS,
    MCP_URLS,
    PROFILE_PARSER_PORT,
)

logger = logging.getLogger(__name__)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)


class ProfileParserAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Profile Parser Agent",
            description=(
                "Parses LinkedIn profiles and resumes into a structured CandidateProfile. "
                "Supports LinkedIn URL, raw text, and PDF input."
            ),
            url=f"http://localhost:{PROFILE_PARSER_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="parse_linkedin_profile",
                    name="Parse LinkedIn Profile",
                    description="Fetch and parse a LinkedIn profile from a public URL.",
                    tags=["linkedin", "profile", "parsing"],
                ),
                AgentSkill(
                    id="parse_resume_text",
                    name="Parse Resume Text",
                    description="Extract structured profile data from raw resume text.",
                    tags=["resume", "parsing"],
                ),
                AgentSkill(
                    id="parse_resume_pdf",
                    name="Parse Resume PDF",
                    description="Extract text from a PDF resume (base64) then parse it.",
                    tags=["resume", "pdf", "parsing"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "parse_linkedin_profile":
            return await self._parse_linkedin(input_data)
        if skill_id == "parse_resume_text":
            return await self._parse_text(input_data.get("text", ""))
        if skill_id == "parse_resume_pdf":
            return await self._parse_pdf(input_data.get("pdf_b64", ""))
        raise ValueError(f"Unknown skill: {skill_id}")

    # ------------------------------------------------------------------
    # LinkedIn profile
    # ------------------------------------------------------------------

    async def _parse_linkedin(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        linkedin_url = input_data.get("linkedin_url", "")
        logger.info("Fetching LinkedIn profile: %s", linkedin_url)

        # Call LinkedIn MCP server
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{MCP_URLS['linkedin']}/",
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tools/call",
                    "params": {
                        "name": "fetch_linkedin_profile",
                        "arguments": {"linkedin_url": linkedin_url},
                    },
                },
            )
            data = resp.json()

        content = data.get("result", {}).get("content", [{}])[0].get("text", "{}")
        profile = json.loads(content)

        # Merge any extra fields from raw resume if provided
        if input_data.get("resume_text"):
            resume_profile = await self._parse_text(input_data["resume_text"])
            # Resume skills / experience enrich the LinkedIn data
            profile.setdefault("skills", [])
            profile["skills"] = list(set(profile["skills"] + resume_profile.get("skills", [])))
            profile["raw_resume_text"] = input_data["resume_text"]

        profile["linkedin_url"] = linkedin_url
        return profile

    # ------------------------------------------------------------------
    # Raw text parsing
    # ------------------------------------------------------------------

    async def _parse_text(self, text: str) -> Dict[str, Any]:
        logger.info("Parsing resume text (%d chars)", len(text))
        response = claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"""Extract a structured candidate profile from the resume text below.
Return ONLY valid JSON — no markdown, no commentary — matching this exact schema:

{{
  "full_name": "string",
  "headline": "string",
  "location": "string",
  "email": "string or null",
  "summary": "string",
  "skills": ["skill1", ...],
  "experience": [
    {{
      "company": "string",
      "title": "string",
      "start_date": "string",
      "end_date": "string or null",
      "description": "string",
      "skills_used": ["string"],
      "achievements": ["string"]
    }}
  ],
  "education": [
    {{
      "institution": "string",
      "degree": "string",
      "field_of_study": "string",
      "end_year": "integer or null"
    }}
  ],
  "certifications": [{{"name":"string","issuer":"string","year":null}}],
  "languages": ["string"],
  "years_of_experience": 0.0,
  "current_title": "string",
  "raw_resume_text": ""
}}

Resume text:
{text[:6000]}""",
            }],
        )
        raw = response.content[0].text
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
        profile = json.loads(raw)
        profile["raw_resume_text"] = text
        return profile

    # ------------------------------------------------------------------
    # PDF parsing (call Document MCP then parse text)
    # ------------------------------------------------------------------

    async def _parse_pdf(self, pdf_b64: str) -> Dict[str, Any]:
        logger.info("Extracting text from PDF resume")
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{MCP_URLS['document']}/",
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tools/call",
                    "params": {
                        "name": "extract_text_from_pdf",
                        "arguments": {"pdf_b64": pdf_b64},
                    },
                },
            )
            data = resp.json()

        content = data.get("result", {}).get("content", [{}])[0].get("text", "{}")
        extracted = json.loads(content)
        text = extracted.get("text", "")
        return await self._parse_text(text)


agent = ProfileParserAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PROFILE_PARSER_PORT, log_level="info")
