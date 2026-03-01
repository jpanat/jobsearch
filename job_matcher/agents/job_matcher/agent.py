"""
Job Matcher Agent
──────────────────
Skills:
  • score_jobs — given a CandidateProfile and a list of JobListings,
                 compute multi-dimensional match scores and return a ranked
                 list of JobMatch objects.

Scoring dimensions (each 0-100):
  - skills_match   : overlap between candidate skills and job required/preferred skills
  - experience_match: YoE alignment and seniority match
  - location_match  : location / work-mode compatibility
  - salary_match    : expectation vs. offered range

Overall = 0.40·skills + 0.30·experience + 0.20·location + 0.10·salary
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from anthropic import AsyncAnthropic

from job_matcher.a2a.protocol import AgentCard, AgentCapabilities, AgentSkill, Task
from job_matcher.a2a.server import BaseA2AAgent
from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    JOB_MATCHER_PORT,
    MAX_TOKENS,
    TOP_N_MATCHES,
)
from job_matcher.shared.models import _extract_json

logger = logging.getLogger(__name__)
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class JobMatcherAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Job Matcher Agent",
            description=(
                "Scores and ranks job listings against a candidate profile across "
                "multiple dimensions: skills, experience, location and salary."
            ),
            url=f"http://localhost:{JOB_MATCHER_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="score_jobs",
                    name="Score Jobs",
                    description="Score and rank a list of jobs against a candidate profile.",
                    tags=["matching", "scoring", "ranking"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "score_jobs":
            return await self._score_jobs(input_data)
        raise ValueError(f"Unknown skill: {skill_id}")

    async def _score_jobs(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data.get("profile", {})
        jobs = input_data.get("jobs", [])
        top_n = input_data.get("top_n", TOP_N_MATCHES)

        logger.info("Scoring %d jobs for %s", len(jobs), profile.get("full_name", "candidate"))

        # Batch jobs through Claude to get scored matches
        # Process in chunks of 10 to stay within context limits
        chunk_size = 10
        all_matches: List[dict] = []

        for i in range(0, len(jobs), chunk_size):
            chunk = jobs[i: i + chunk_size]
            matches = await self._score_chunk(profile, chunk)
            all_matches.extend(matches)

        # Sort by overall_score descending
        all_matches.sort(key=lambda m: m.get("overall_score", 0), reverse=True)
        top_matches = all_matches[:top_n]

        return {"matches": top_matches, "total_scored": len(all_matches)}

    async def _score_chunk(self, profile: dict, jobs: List[dict]) -> List[dict]:
        candidate_summary = {
            "full_name": profile.get("full_name", ""),
            "headline": profile.get("headline", ""),
            "skills": profile.get("skills", []),
            "years_of_experience": profile.get("years_of_experience", 0),
            "location": profile.get("location", ""),
            "work_mode_preference": profile.get("work_mode_preference"),
            "salary_expectation_usd": profile.get("salary_expectation_usd"),
            "desired_roles": profile.get("desired_roles", []),
        }

        jobs_summary = []
        for j in jobs:
            jobs_summary.append({
                "job_id": j.get("job_id", ""),
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "work_mode": j.get("work_mode"),
                "salary_min": j.get("salary_min"),
                "salary_max": j.get("salary_max"),
                "required_skills": j.get("required_skills", []),
                "preferred_skills": j.get("preferred_skills", []),
                "experience_level": j.get("experience_level"),
                "description_excerpt": j.get("description", "")[:500],
            })

        response = await claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"""You are a job-matching specialist. Score each job listing against the candidate profile.

CANDIDATE:
{json.dumps(candidate_summary, indent=2)}

JOBS:
{json.dumps(jobs_summary, indent=2)}

For each job compute:
- skills_match_score (0-100): % of required_skills the candidate has, plus bonus for preferred
- experience_match_score (0-100): YoE alignment and seniority fit
- location_match_score (0-100): 100 if remote/matching preference, 0 if mismatch
- salary_match_score (0-100): 100 if salary range meets or exceeds expectation, 0 if far below
- overall_score = 0.40*skills + 0.30*experience + 0.20*location + 0.10*salary
- matching_skills: list of skills candidate has that match the job
- missing_skills: list of required skills the candidate is missing
- match_rationale: 1-2 sentence explanation

Return ONLY a JSON array (one object per job):
[
  {{
    "job_id": "...",
    "overall_score": 82.5,
    "skills_match_score": 90,
    "experience_match_score": 85,
    "location_match_score": 100,
    "salary_match_score": 70,
    "matching_skills": ["Python", "FastAPI"],
    "missing_skills": ["Kubernetes"],
    "match_rationale": "Strong skills match with minor gap in cloud orchestration."
  }}
]
""",
            }],
        )

        scored = _extract_json(response.content[0].text)

        # Merge scores back into full job objects
        job_by_id = {j.get("job_id", ""): j for j in jobs}
        matches = []
        for s in scored:
            job_id = s.get("job_id", "")
            job = job_by_id.get(job_id, {})
            matches.append({
                "job": job,
                "overall_score": s.get("overall_score", 0),
                "skills_match_score": s.get("skills_match_score", 0),
                "experience_match_score": s.get("experience_match_score", 0),
                "location_match_score": s.get("location_match_score", 0),
                "salary_match_score": s.get("salary_match_score", 0),
                "matching_skills": s.get("matching_skills", []),
                "missing_skills": s.get("missing_skills", []),
                "match_rationale": s.get("match_rationale", ""),
            })
        return matches


agent = JobMatcherAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=JOB_MATCHER_PORT, log_level="info")
