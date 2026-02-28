"""
Resume Customizer Agent
────────────────────────
Skills:
  • customize_resume — rewrite a candidate resume for a specific job listing,
                       injecting job keywords, reordering experience bullets,
                       and estimating an ATS compatibility score.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from anthropic import Anthropic

from job_matcher.a2a.protocol import AgentCard, AgentCapabilities, AgentSkill, Task
from job_matcher.a2a.server import BaseA2AAgent
from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    MAX_TOKENS,
    RESUME_CUSTOMIZER_PORT,
)

logger = logging.getLogger(__name__)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)


class ResumeCustmizerAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Resume Customizer Agent",
            description=(
                "Rewrites and optimises a candidate's resume for a specific job listing. "
                "Injects relevant keywords, adjusts tone, and estimates ATS score."
            ),
            url=f"http://localhost:{RESUME_CUSTOMIZER_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="customize_resume",
                    name="Customize Resume",
                    description="Tailor a resume to a specific job listing and return a CustomizedResume.",
                    tags=["resume", "ats", "customization"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "customize_resume":
            return await self._customize(input_data)
        raise ValueError(f"Unknown skill: {skill_id}")

    async def _customize(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data.get("profile", {})
        job = input_data.get("job", {})

        logger.info(
            "Customizing resume for %s → %s at %s",
            profile.get("full_name", ""),
            job.get("title", ""),
            job.get("company", ""),
        )

        response = claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"""You are a professional resume writer and ATS expert.

TASK: Rewrite the candidate's resume to be optimally tailored for the target job.

CANDIDATE PROFILE:
{json.dumps(profile, indent=2)}

TARGET JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Required Skills: {json.dumps(job.get('required_skills', []))}
Preferred Skills: {json.dumps(job.get('preferred_skills', []))}
Description (excerpt):
{job.get('description', '')[:2000]}

INSTRUCTIONS:
1. Rewrite the professional summary to align with the job.
2. Reorder and enhance experience bullets to highlight relevant skills first.
3. Inject job-specific keywords naturally throughout.
4. Quantify achievements where possible.
5. Remove or de-emphasise irrelevant experience.
6. Estimate an ATS compatibility score (0-100).

Return ONLY valid JSON matching this schema:
{{
  "job_id": "{job.get('job_id', '')}",
  "candidate_name": "{profile.get('full_name', '')}",
  "target_title": "{job.get('title', '')}",
  "summary": "Rewritten professional summary...",
  "sections": [
    {{
      "heading": "Work Experience",
      "content": "Formatted markdown content..."
    }},
    {{
      "heading": "Skills",
      "content": "Formatted skills list..."
    }},
    {{
      "heading": "Education",
      "content": "Formatted education..."
    }}
  ],
  "keywords_added": ["keyword1", "keyword2"],
  "ats_score_estimate": 87.5,
  "markdown_content": "Full resume in clean markdown..."
}}
""",
            }],
        )

        raw = re.sub(r"```(?:json)?", "", response.content[0].text).strip().rstrip("```").strip()
        return json.loads(raw)


agent = ResumeCustmizerAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=RESUME_CUSTOMIZER_PORT, log_level="info")
