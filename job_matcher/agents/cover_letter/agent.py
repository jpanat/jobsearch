"""
Cover Letter Agent
───────────────────
Skills:
  • generate_cover_letter — write a tailored, compelling cover letter for a
                            specific job listing and candidate profile.
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
    COVER_LETTER_PORT,
    DEFAULT_MODEL,
    MAX_TOKENS,
)
from job_matcher.shared.models import _extract_json

logger = logging.getLogger(__name__)
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class CoverLetterAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Cover Letter Agent",
            description=(
                "Generates personalised, compelling cover letters tailored to a "
                "specific job and candidate. Supports multiple tones and styles."
            ),
            url=f"http://localhost:{COVER_LETTER_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="generate_cover_letter",
                    name="Generate Cover Letter",
                    description="Write a tailored cover letter for a job application.",
                    tags=["cover_letter", "writing"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "generate_cover_letter":
            return await self._generate(input_data)
        raise ValueError(f"Unknown skill: {skill_id}")

    async def _generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data.get("profile", {})
        job = input_data.get("job", {})
        match = input_data.get("match", {})
        tone = input_data.get("tone", "professional")
        hiring_manager = input_data.get("hiring_manager", "")

        matching_skills = match.get("matching_skills", profile.get("skills", [])[:5])
        match_rationale = match.get("match_rationale", "")

        logger.info(
            "Generating cover letter for %s → %s at %s (tone: %s)",
            profile.get("full_name", ""),
            job.get("title", ""),
            job.get("company", ""),
            tone,
        )

        response = await claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"""You are an expert career coach writing cover letters.

TASK: Write a personalised, compelling cover letter for this job application.

CANDIDATE:
Name: {profile.get('full_name', '')}
Headline: {profile.get('headline', '')}
Summary: {profile.get('summary', '')}
Key Matching Skills: {json.dumps(matching_skills)}
Top Achievement (from experience): {profile.get('experience', [{}])[0].get('achievements', [''])[0] if profile.get('experience') else ''}

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Description excerpt: {job.get('description', '')[:1500]}
Required Skills: {json.dumps(job.get('required_skills', []))}

WHY THEY FIT: {match_rationale}
HIRING MANAGER: {hiring_manager or 'Hiring Team'}
TONE: {tone}  (professional | enthusiastic | concise)

COVER LETTER REQUIREMENTS:
- 3-4 paragraphs, under 400 words
- Open with a hook that references the company specifically
- Show you understand their mission / product
- Highlight 2-3 concrete achievements that directly map to job requirements
- Close with a clear call to action
- Tone: {tone}

Return ONLY valid JSON:
{{
  "job_id": "{job.get('job_id', '')}",
  "candidate_name": "{profile.get('full_name', '')}",
  "company_name": "{job.get('company', '')}",
  "hiring_manager": "{hiring_manager or 'Hiring Team'}",
  "subject_line": "Application for {job.get('title', '')} — [Candidate Name]",
  "body": "Full letter body (3-4 paragraphs, plain text)...",
  "call_to_action": "Closing sentence requesting an interview...",
  "tone": "{tone}"
}}
""",
            }],
        )

        return _extract_json(response.content[0].text)


agent = CoverLetterAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=COVER_LETTER_PORT, log_level="info")
