"""
Interview Prep Agent
─────────────────────
Skills:
  • prepare_interview — given a candidate profile + job listing, generate a
                        comprehensive InterviewPrepKit containing:
                          • Company research summary
                          • Behavioral, technical and situational questions + sample answers
                          • STAR story prompts based on the candidate's experience
                          • Smart questions to ask the interviewer
                          • Salary negotiation tips
                          • Red flags to watch for
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
    INTERVIEW_PREP_PORT,
    MAX_TOKENS,
    TAVILY_API_KEY,
)
from job_matcher.shared.models import _extract_json

logger = logging.getLogger(__name__)
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class InterviewPrepAgent(BaseA2AAgent):

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name="Interview Prep Agent",
            description=(
                "Creates a full interview preparation kit for a candidate applying "
                "to a specific role. Includes company research, predicted questions, "
                "STAR stories, and negotiation tips."
            ),
            url=f"http://localhost:{INTERVIEW_PREP_PORT}",
            capabilities=AgentCapabilities(state_transition_history=True),
            skills=[
                AgentSkill(
                    id="prepare_interview",
                    name="Prepare Interview",
                    description="Generate a complete InterviewPrepKit for a job application.",
                    tags=["interview", "prep", "questions", "star"],
                ),
            ],
        )

    async def handle_skill(
        self, skill_id: str, input_data: Dict[str, Any], task: Task
    ) -> Dict[str, Any]:
        if skill_id == "prepare_interview":
            return await self._prepare(input_data)
        raise ValueError(f"Unknown skill: {skill_id}")

    async def _prepare(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data.get("profile", {})
        job = input_data.get("job", {})
        match = input_data.get("match", {})
        gap_report = input_data.get("gap_report", {})

        logger.info(
            "Preparing interview kit for %s → %s at %s",
            profile.get("full_name", ""),
            job.get("title", ""),
            job.get("company", ""),
        )

        # Optionally fetch fresh company info via Tavily
        company_research = await self._research_company(job.get("company", ""))

        response = await claude.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": f"""You are an elite interview coach preparing a candidate for a job interview.

CANDIDATE:
{json.dumps(profile, indent=2)}

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Required Skills: {json.dumps(job.get('required_skills', []))}
Description: {job.get('description', '')[:2000]}

COMPANY RESEARCH:
{company_research}

MATCH CONTEXT:
Matching Skills: {json.dumps(match.get('matching_skills', []))}
Missing Skills: {json.dumps(match.get('missing_skills', []))}
Skill Gaps to Address: {json.dumps([g.get('skill','') for g in gap_report.get('skill_gaps', [])])}

CANDIDATE STRENGTHS: {json.dumps(gap_report.get('strengths', []))}

Create a comprehensive interview preparation kit. Return ONLY valid JSON:
{{
  "job_id": "{job.get('job_id', '')}",
  "company_name": "{job.get('company', '')}",
  "role_title": "{job.get('title', '')}",
  "company_research": "3-4 paragraph company overview with mission, products, recent news...",
  "questions": [
    {{
      "question": "Tell me about a time you improved system performance significantly.",
      "category": "behavioral",
      "difficulty": "medium",
      "sample_answer": "STAR-formatted answer using candidate's actual experience...",
      "tips": ["Use specific metrics", "Emphasize collaboration"]
    }},
    {{
      "question": "Explain how you would design a distributed caching system.",
      "category": "technical",
      "difficulty": "hard",
      "sample_answer": "Structured technical answer...",
      "tips": ["Start with requirements", "Draw the architecture mentally first"]
    }}
  ],
  "star_story_prompts": [
    "Describe a time you led a project under tight deadlines — use your experience at [Company X]",
    "Tell a story about debugging a critical production issue"
  ],
  "questions_to_ask_interviewer": [
    "What does success look like in the first 90 days?",
    "How does the team handle technical debt?",
    "What are the biggest challenges the team is facing right now?"
  ],
  "salary_negotiation_tips": "Practical tips for negotiating the salary offer...",
  "red_flags_to_watch": [
    "Vague answers about team size or reporting structure",
    "No clear onboarding plan"
  ]
}}

Include at least 8 questions: mix of behavioral (4), technical (3), situational (1).
""",
            }],
        )

        kit = _extract_json(response.content[0].text)
        # Inject live company research if we fetched it
        if company_research and not kit.get("company_research"):
            kit["company_research"] = company_research
        return kit

    async def _research_company(self, company_name: str) -> str:
        if not company_name or not TAVILY_API_KEY:
            return f"Research on {company_name} not available (no Tavily API key)."
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": f"{company_name} company overview mission products 2025",
                        "max_results": 3,
                    },
                )
                data = resp.json()
            snippets = [r.get("content", "") for r in data.get("results", [])]
            return "\n\n".join(snippets)[:2000]
        except Exception as exc:
            logger.warning("Company research failed: %s", exc)
            return f"Could not fetch live data for {company_name}."


agent = InterviewPrepAgent()
app = agent.app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=INTERVIEW_PREP_PORT, log_level="info")
