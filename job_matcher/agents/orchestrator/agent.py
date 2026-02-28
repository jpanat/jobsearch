"""
Orchestrator Agent
───────────────────
The central brain of the Job Matcher system.

Responsibilities:
  1. Accept the user request (LinkedIn URL + optional resume + job preferences).
  2. Delegate parsing to Profile Parser Agent via A2A.
  3. Delegate job discovery to Job Discovery Agent via A2A.
  4. Delegate scoring to Job Matcher Agent via A2A.
  5. For the top N matches, fan out IN PARALLEL to:
       • Resume Customizer Agent
       • Cover Letter Agent
       • Gap Analysis Agent
  6. For each completed gap report, call Interview Prep Agent.
  7. Aggregate and return the full JobMatcherPipelineResult.

The orchestrator also exposes a REST API (POST /run) for direct integration
with the Streamlit UI and CLI without going through A2A.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
from anthropic import Anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from job_matcher.a2a.client import A2AClient
from job_matcher.a2a.protocol import AgentCard, AgentCapabilities, AgentSkill, Task
from job_matcher.a2a.server import BaseA2AAgent
from job_matcher.shared.config import (
    AGENT_URLS,
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    GENERATE_DOCS_FOR_TOP_N,
    MAX_TOKENS,
    ORCHESTRATOR_PORT,
    TOP_N_MATCHES,
)

logger = logging.getLogger(__name__)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Request / Response models for the REST API
# ---------------------------------------------------------------------------

class PipelineRequest(BaseModel):
    linkedin_url: Optional[str] = None
    resume_text: Optional[str] = None
    resume_pdf_b64: Optional[str] = None
    location_override: Optional[str] = None
    remote_ok: bool = True
    desired_roles: List[str] = []
    salary_expectation_usd: Optional[int] = None
    tone: str = "professional"
    top_n: int = TOP_N_MATCHES
    generate_docs_for_top_n: int = GENERATE_DOCS_FOR_TOP_N


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class OrchestratorAgent(BaseA2AAgent):

    def __init__(self) -> None:
        super().__init__()
        # Mount the REST API on top of the A2A app
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._register_rest_routes()

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Job Matcher Orchestrator",
            description=(
                "Master coordinator for the Job Matcher multi-agent pipeline. "
                "Accepts a LinkedIn profile and/or resume, then coordinates all "
                "specialist agents to discover jobs, score fit, customise resumes, "
                "write cover letters, identify gaps, and prepare for interviews."
            ),
            url=f"http://localhost:{ORCHESTRATOR_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="run_pipeline",
                    name="Run Full Pipeline",
                    description=(
                        "Execute the complete job matching pipeline for a candidate."
                    ),
                    tags=["orchestration", "pipeline"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "run_pipeline":
            req = PipelineRequest(**input_data)
            return await self._run_pipeline(req)
        raise ValueError(f"Unknown skill: {skill_id}")

    # ------------------------------------------------------------------
    # REST API (for UI/CLI)
    # ------------------------------------------------------------------

    def _register_rest_routes(self) -> None:
        @self.app.post("/run")
        async def run_pipeline(request: PipelineRequest):
            try:
                return await self._run_pipeline(request)
            except Exception as exc:
                logger.exception("Pipeline error")
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.get("/agents")
        async def list_agents():
            """Return agent cards for all registered agents."""
            cards = {}
            for name, url in AGENT_URLS.items():
                try:
                    async with A2AClient(url) as client:
                        card = await client.get_agent_card()
                        cards[name] = card.model_dump()
                except Exception:
                    cards[name] = {"error": "unreachable"}
            return cards

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, req: PipelineRequest) -> Dict[str, Any]:
        trace: List[str] = []

        def log(msg: str) -> None:
            logger.info(msg)
            trace.append(msg)

        # ── Step 1: Parse profile ──────────────────────────────────────
        log("Step 1/5 → Profile Parser Agent: parsing candidate profile")
        profile = await self._call_profile_parser(req)
        if req.desired_roles:
            profile["desired_roles"] = req.desired_roles
        if req.salary_expectation_usd:
            profile["salary_expectation_usd"] = req.salary_expectation_usd
        if req.remote_ok:
            profile["work_mode_preference"] = "remote"
        log(f"  Profile parsed: {profile.get('full_name','unknown')} | "
            f"{len(profile.get('skills',[]))} skills | "
            f"{profile.get('years_of_experience',0):.1f} YoE")

        # ── Step 2: Discover jobs ──────────────────────────────────────
        log("Step 2/5 → Job Discovery Agent: scanning job boards")
        discovery = await self._call_job_discovery(req, profile)
        all_jobs = discovery.get("jobs", [])
        log(f"  Discovered {len(all_jobs)} unique jobs across all boards")

        # ── Step 3: Score & rank ───────────────────────────────────────
        log("Step 3/5 → Job Matcher Agent: scoring fit")
        scoring = await self._call_job_matcher(profile, all_jobs, req.top_n)
        top_matches = scoring.get("matches", [])[:req.generate_docs_for_top_n]
        log(f"  Top match: {top_matches[0]['job'].get('title','') if top_matches else 'none'} "
            f"(score: {top_matches[0].get('overall_score',0):.1f})" if top_matches else "  No matches found")

        # ── Step 4: Parallel document generation ──────────────────────
        log(f"Step 4/5 → Parallel generation for top {len(top_matches)} matches")
        doc_results = await asyncio.gather(
            *[self._generate_docs(profile, m, req.tone, trace) for m in top_matches],
            return_exceptions=True,
        )

        resumes, cover_letters, gap_reports, interview_kits = [], [], [], []
        for r in doc_results:
            if isinstance(r, Exception):
                log(f"  Warning: document generation failed — {r}")
                continue
            resumes.append(r.get("resume", {}))
            cover_letters.append(r.get("cover_letter", {}))
            gap_reports.append(r.get("gap_report", {}))
            interview_kits.append(r.get("interview_kit", {}))

        log("Step 5/5 → Pipeline complete")

        return {
            "profile": profile,
            "top_matches": scoring.get("matches", [])[:req.top_n],
            "customized_resumes": resumes,
            "cover_letters": cover_letters,
            "gap_reports": gap_reports,
            "interview_kits": interview_kits,
            "agent_trace": trace,
            "status": "complete",
        }

    # ------------------------------------------------------------------
    # Per-match parallel document generation
    # ------------------------------------------------------------------

    async def _generate_docs(
        self, profile: dict, match: dict, tone: str, trace: List[str]
    ) -> Dict[str, Any]:
        job = match.get("job", {})
        title = job.get("title", "")
        company = job.get("company", "")
        trace.append(f"  Generating docs for: {title} @ {company}")

        # Resume, Cover Letter, Gap Analysis in parallel
        resume, cover_letter, gap_report = await asyncio.gather(
            self._call_resume_customizer(profile, job),
            self._call_cover_letter(profile, job, match, tone),
            self._call_gap_analysis(profile, job, match),
        )

        # Interview prep depends on gap report
        interview_kit = await self._call_interview_prep(profile, job, match, gap_report)

        trace.append(f"  Docs complete for: {title} @ {company}")
        return {
            "resume": resume,
            "cover_letter": cover_letter,
            "gap_report": gap_report,
            "interview_kit": interview_kit,
        }

    # ------------------------------------------------------------------
    # A2A calls to specialist agents
    # ------------------------------------------------------------------

    async def _a2a(self, agent_name: str, skill_id: str, input_data: dict) -> dict:
        url = AGENT_URLS[agent_name]
        async with A2AClient(url) as client:
            task = await client.send_task(skill_id=skill_id, input_data=input_data)
            return A2AClient.get_artifact(task, skill_id) or {}

    async def _call_profile_parser(self, req: PipelineRequest) -> dict:
        if req.linkedin_url:
            return await self._a2a(
                "profile_parser", "parse_linkedin_profile",
                {"linkedin_url": req.linkedin_url, "resume_text": req.resume_text or ""},
            )
        if req.resume_pdf_b64:
            return await self._a2a(
                "profile_parser", "parse_resume_pdf",
                {"pdf_b64": req.resume_pdf_b64},
            )
        if req.resume_text:
            return await self._a2a(
                "profile_parser", "parse_resume_text",
                {"text": req.resume_text},
            )
        raise ValueError("Provide at least one of: linkedin_url, resume_text, resume_pdf_b64")

    async def _call_job_discovery(self, req: PipelineRequest, profile: dict) -> dict:
        return await self._a2a(
            "job_discovery", "discover_jobs",
            {
                "profile": profile,
                "location_override": req.location_override or "",
                "remote_ok": req.remote_ok,
            },
        )

    async def _call_job_matcher(self, profile: dict, jobs: list, top_n: int) -> dict:
        return await self._a2a(
            "job_matcher", "score_jobs",
            {"profile": profile, "jobs": jobs, "top_n": top_n},
        )

    async def _call_resume_customizer(self, profile: dict, job: dict) -> dict:
        return await self._a2a(
            "resume_customizer", "customize_resume",
            {"profile": profile, "job": job},
        )

    async def _call_cover_letter(self, profile: dict, job: dict, match: dict, tone: str) -> dict:
        return await self._a2a(
            "cover_letter", "generate_cover_letter",
            {"profile": profile, "job": job, "match": match, "tone": tone},
        )

    async def _call_gap_analysis(self, profile: dict, job: dict, match: dict) -> dict:
        return await self._a2a(
            "gap_analysis", "analyze_gaps",
            {"profile": profile, "job": job, "match": match},
        )

    async def _call_interview_prep(
        self, profile: dict, job: dict, match: dict, gap_report: dict
    ) -> dict:
        return await self._a2a(
            "interview_prep", "prepare_interview",
            {"profile": profile, "job": job, "match": match, "gap_report": gap_report},
        )


# ---------------------------------------------------------------------------

agent = OrchestratorAgent()
app = agent.app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=ORCHESTRATOR_PORT, log_level="info")
