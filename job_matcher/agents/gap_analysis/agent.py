"""
Gap Analysis Agent
───────────────────
Skills:
  • analyze_gaps — compares a candidate profile against a job listing and
                   produces a detailed GapAnalysisReport covering:
                     • skill gaps (with importance + learning path)
                     • experience gaps
                     • resume improvement suggestions
                     • strengths and quick wins
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from anthropic import AsyncAnthropic

from job_matcher.a2a.protocol import AgentCard, AgentCapabilities, AgentSkill, Task
from job_matcher.a2a.server import BaseA2AAgent
from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    GAP_ANALYSIS_PORT,
    MAX_TOKENS,
)
from job_matcher.shared.models import _extract_json

logger = logging.getLogger(__name__)
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class GapAnalysisAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Gap Analysis Agent",
            description=(
                "Analyses the gap between a candidate's profile and a job listing. "
                "Identifies missing skills, experience gaps, and provides an actionable "
                "roadmap to increase competitiveness for the role."
            ),
            url=f"http://localhost:{GAP_ANALYSIS_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="analyze_gaps",
                    name="Analyze Gaps",
                    description="Produce a GapAnalysisReport for a profile vs. a job.",
                    tags=["gap_analysis", "skills", "resume"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "analyze_gaps":
            return await self._analyze(input_data)
        raise ValueError(f"Unknown skill: {skill_id}")

    async def _analyze(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data.get("profile", {})
        job = input_data.get("job", {})
        match = input_data.get("match", {})

        logger.info(
            "Analyzing gaps for %s → %s at %s",
            profile.get("full_name", ""),
            job.get("title", ""),
            job.get("company", ""),
        )

        missing_skills = match.get("missing_skills", [])
        overall_score = match.get("overall_score", 0)

        response = await claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"""You are a senior career coach and skills assessor.

TASK: Perform a deep gap analysis between this candidate and job, then create an actionable roadmap.

CANDIDATE PROFILE:
{json.dumps(profile, indent=2)}

TARGET JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Required Skills: {json.dumps(job.get('required_skills', []))}
Preferred Skills: {json.dumps(job.get('preferred_skills', []))}
Requirements: {json.dumps(job.get('requirements', []))}
Description: {job.get('description', '')[:2000]}

ALREADY IDENTIFIED MISSING SKILLS: {json.dumps(missing_skills)}
CURRENT MATCH SCORE: {overall_score}/100

Return ONLY valid JSON matching this schema:
{{
  "job_id": "{job.get('job_id', '')}",
  "overall_readiness_score": 75.0,
  "skill_gaps": [
    {{
      "skill": "Kubernetes",
      "importance": "critical",
      "how_to_acquire": "Complete the CKAD certification on Linux Foundation",
      "estimated_time_weeks": 8,
      "resources": ["https://training.linuxfoundation.org/certification/certified-kubernetes-application-developer-ckad/", "Kubernetes in Action (book)"]
    }}
  ],
  "experience_gaps": [
    "No experience managing a team of >5 engineers",
    "Limited exposure to enterprise-scale deployments"
  ],
  "strengths": [
    "Strong Python backend skills align directly with tech stack",
    "Open source contributions demonstrate initiative"
  ],
  "quick_wins": [
    "Add Docker Compose examples to GitHub portfolio to demonstrate container knowledge",
    "Obtain AWS Solutions Architect Associate cert (1-2 months)"
  ],
  "long_term_actions": [
    "Build and publish a side project using the target company's tech stack",
    "Contribute to an open-source project in the ML ops space"
  ],
  "resume_improvements": [
    "Quantify impact at Company X (e.g., 'reduced latency by 40%')",
    "Add a Skills section at the top since recruiters scan for keywords",
    "Move Python and FastAPI to the top of the skills list",
    "Add GitHub and portfolio links to the header"
  ]
}}
""",
            }],
        )

        return _extract_json(response.content[0].text)


agent = GapAnalysisAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=GAP_ANALYSIS_PORT, log_level="info")
