"""
Job Discovery Agent
────────────────────
Skills:
  • discover_jobs — given a CandidateProfile, search Indeed, Glassdoor and
                    LinkedIn Jobs and return a de-duplicated list of JobListings.

Strategy:
  1. Claude synthesises 3-5 role-specific search queries from the profile.
  2. Queries are fired in parallel against all three job boards via Job Boards MCP.
  3. Results are de-duplicated by (title, company) and stored in Memory MCP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List

import httpx
from anthropic import AsyncAnthropic

from job_matcher.a2a.protocol import AgentCard, AgentCapabilities, AgentSkill, Task
from job_matcher.a2a.server import BaseA2AAgent
from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    JOB_DISCOVERY_PORT,
    MAX_JOBS_TO_DISCOVER,
    MAX_TOKENS,
    MCP_URLS,
)
from job_matcher.shared.models import _extract_json, _parse_mcp_result

logger = logging.getLogger(__name__)
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class JobDiscoveryAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Job Discovery Agent",
            description=(
                "Searches Indeed, Glassdoor, and LinkedIn Jobs for open positions "
                "that match a candidate profile. Returns a ranked list of JobListings."
            ),
            url=f"http://localhost:{JOB_DISCOVERY_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="discover_jobs",
                    name="Discover Jobs",
                    description="Search all job boards and return deduplicated JobListings for a candidate.",
                    tags=["job_search", "indeed", "glassdoor", "linkedin"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "discover_jobs":
            return await self._discover_jobs(input_data)
        raise ValueError(f"Unknown skill: {skill_id}")

    # ------------------------------------------------------------------

    async def _discover_jobs(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data.get("profile", {})
        location = input_data.get("location_override", profile.get("location", ""))
        remote_ok = input_data.get("remote_ok", True)
        max_jobs = input_data.get("max_results", MAX_JOBS_TO_DISCOVER)

        # Step 1: Generate search queries from profile
        queries = await self._generate_queries(profile)
        logger.info("Generated %d search queries", len(queries))

        # Step 2: Fan out to all five job boards (3 paid/Tavily + 2 always-free) in parallel
        paid_boards = ["indeed", "glassdoor", "linkedin"]
        free_boards = ["arbeitnow", "remoteok"]
        all_boards = paid_boards + free_boards
        search_tasks = []
        for q in queries:
            n_per = max_jobs // (len(queries) * len(all_boards)) + 1
            for board in all_boards:
                search_tasks.append(self._call_board(board, q, location, remote_ok, n_per))

        results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Step 3: Flatten, deduplicate, store in memory
        all_jobs: List[dict] = []
        seen = set()
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Board search error: %s", r)
                continue
            for job in r.get("jobs", []):
                key = (job.get("title", "").lower(), job.get("company", "").lower())
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(job)

        all_jobs = all_jobs[:max_jobs]

        # Store each job in Memory MCP
        await asyncio.gather(*[self._store_job(job) for job in all_jobs], return_exceptions=True)

        logger.info("Discovered %d unique jobs", len(all_jobs))
        return {"jobs": all_jobs, "total": len(all_jobs), "queries_used": queries}

    async def _generate_queries(self, profile: dict) -> List[str]:
        headline = profile.get("headline", "")
        current_title = profile.get("current_title", "")
        skills = profile.get("skills", [])[:10]
        desired = profile.get("desired_roles", [])

        response = await claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"""Generate 4 job search queries for a candidate with this profile:
Headline: {headline}
Current Title: {current_title}
Top Skills: {', '.join(skills)}
Desired Roles: {', '.join(desired)}

Return ONLY a JSON array of 4 concise search strings, e.g.:
["Senior Python Engineer", "Backend Engineer FastAPI", "ML Engineer PyTorch", "Staff Software Engineer AI"]
""",
            }],
        )
        return _extract_json(response.content[0].text)

    async def _call_board(
        self, board: str, query: str, location: str, remote_ok: bool, n: int
    ) -> dict:
        tool_map = {
            "indeed": "search_indeed",
            "glassdoor": "search_glassdoor",
            "linkedin": "search_linkedin_jobs",
            "arbeitnow": "search_arbeitnow",
            "remoteok": "search_remoteok",
        }
        tool = tool_map[board]
        # Free boards (arbeitnow, remoteok) only accept query + max_results
        if board in ("arbeitnow", "remoteok"):
            args: dict = {"query": query, "max_results": n}
        else:
            args = {"query": query, "location": location, "max_results": n}
            if board in ("indeed", "linkedin"):
                args["remote_only"] = False  # get both; filter later if needed

        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{MCP_URLS['job_boards']}/",
                json={
                    "jsonrpc": "2.0", "id": "1",
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                },
            )
            data = resp.json()

        return _parse_mcp_result(data)

    async def _store_job(self, job: dict) -> None:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(
                f"{MCP_URLS['memory']}/",
                json={
                    "jsonrpc": "2.0", "id": "1",
                    "method": "tools/call",
                    "params": {"name": "upsert_job", "arguments": {"job_json": job}},
                },
            )


agent = JobDiscoveryAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=JOB_DISCOVERY_PORT, log_level="info")
