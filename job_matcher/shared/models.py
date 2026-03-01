"""
Shared Pydantic models for the Job Matcher multi-agent system.
All agents and MCP servers use these types to ensure type-safe inter-agent communication.
"""

from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum


def _extract_json(text: str):
    """
    Robustly extract and parse the first JSON object or array from LLM output.
    Handles prose preamble/postamble that Claude sometimes adds despite instructions.
    """
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    obj_i = text.find('{')
    arr_i = text.find('[')
    if obj_i == -1 and arr_i == -1:
        return json.loads(text)  # raises naturally with a useful message
    if arr_i == -1 or (obj_i >= 0 and obj_i < arr_i):
        end = text.rfind('}')
        if end == -1 or end < obj_i:
            return json.loads(text)  # no closing brace — let json raise clearly
        return json.loads(text[obj_i: end + 1])
    end = text.rfind(']')
    if end == -1 or end < arr_i:
        return json.loads(text)  # no closing bracket — let json raise clearly
    return json.loads(text[arr_i: end + 1])


def _parse_mcp_result(data: dict):
    """
    Extract and parse JSON from an MCP tool response dict.
    Raises RuntimeError if the tool returned isError=true.
    """
    result = data.get("result", {})
    content_list = result.get("content") or [{}]
    text = content_list[0].get("text", "{}") if content_list else "{}"
    if result.get("isError"):
        raise RuntimeError(f"MCP tool error: {text[:300]}")
    return _extract_json(text)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ExperienceLevel(str, Enum):
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    EXECUTIVE = "executive"


class JobType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    INTERNSHIP = "internship"


class WorkMode(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ON_SITE = "on_site"


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    COMPLETE = "complete"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Profile models
# ---------------------------------------------------------------------------

class WorkExperience(BaseModel):
    company: str
    title: str
    start_date: str
    end_date: Optional[str] = None           # None means "present"
    description: str = ""
    skills_used: List[str] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str
    field_of_study: str
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    gpa: Optional[float] = None


class Certification(BaseModel):
    name: str
    issuer: str
    year: Optional[int] = None


class CandidateProfile(BaseModel):
    """Parsed representation of a LinkedIn profile + optional resume."""
    full_name: str = ""
    headline: str = ""
    location: str = ""
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    summary: str = ""
    skills: List[str] = Field(default_factory=list)
    experience: List[WorkExperience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    years_of_experience: float = 0.0
    current_title: str = ""
    desired_roles: List[str] = Field(default_factory=list)
    desired_locations: List[str] = Field(default_factory=list)
    work_mode_preference: Optional[WorkMode] = None
    salary_expectation_usd: Optional[int] = None
    raw_resume_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Job listing models
# ---------------------------------------------------------------------------

class JobListing(BaseModel):
    """A single job opening discovered from a job board."""
    job_id: str
    title: str
    company: str
    location: str
    work_mode: Optional[WorkMode] = None
    job_type: Optional[JobType] = None
    experience_level: Optional[ExperienceLevel] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    description: str
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    requirements: List[str] = Field(default_factory=list)
    responsibilities: List[str] = Field(default_factory=list)
    benefits: List[str] = Field(default_factory=list)
    posted_date: Optional[str] = None
    apply_url: str
    source: str                              # "indeed" | "glassdoor" | "linkedin"


class JobMatch(BaseModel):
    """A job listing enriched with match scoring against a candidate profile."""
    job: JobListing
    overall_score: float = Field(ge=0, le=100)
    skills_match_score: float = Field(ge=0, le=100)
    experience_match_score: float = Field(ge=0, le=100)
    location_match_score: float = Field(ge=0, le=100)
    salary_match_score: float = Field(ge=0, le=100)
    matching_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    match_rationale: str = ""


# ---------------------------------------------------------------------------
# Resume / document models
# ---------------------------------------------------------------------------

class ResumeSection(BaseModel):
    heading: str
    content: str


class CustomizedResume(BaseModel):
    """Resume rewritten and optimised for a specific job."""
    job_id: str
    candidate_name: str
    target_title: str
    summary: str
    sections: List[ResumeSection] = Field(default_factory=list)
    keywords_added: List[str] = Field(default_factory=list)
    ats_score_estimate: float = Field(ge=0, le=100)
    markdown_content: str = ""


class CoverLetter(BaseModel):
    """Tailored cover letter for a specific job."""
    job_id: str
    candidate_name: str
    company_name: str
    hiring_manager: Optional[str] = None
    subject_line: str
    body: str
    call_to_action: str
    tone: str = "professional"              # professional | enthusiastic | concise


# ---------------------------------------------------------------------------
# Gap analysis models
# ---------------------------------------------------------------------------

class SkillGap(BaseModel):
    skill: str
    importance: str                          # "critical" | "important" | "nice_to_have"
    how_to_acquire: str
    estimated_time_weeks: Optional[int] = None
    resources: List[str] = Field(default_factory=list)


class GapAnalysisReport(BaseModel):
    job_id: str
    overall_readiness_score: float = Field(ge=0, le=100)
    skill_gaps: List[SkillGap] = Field(default_factory=list)
    experience_gaps: List[str] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    quick_wins: List[str] = Field(default_factory=list)
    long_term_actions: List[str] = Field(default_factory=list)
    resume_improvements: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Interview preparation models
# ---------------------------------------------------------------------------

class InterviewQuestion(BaseModel):
    question: str
    category: str                            # "behavioral" | "technical" | "situational"
    difficulty: str                          # "easy" | "medium" | "hard"
    sample_answer: str
    tips: List[str] = Field(default_factory=list)


class InterviewPrepKit(BaseModel):
    job_id: str
    company_name: str
    role_title: str
    company_research: str
    questions: List[InterviewQuestion] = Field(default_factory=list)
    star_story_prompts: List[str] = Field(default_factory=list)
    questions_to_ask_interviewer: List[str] = Field(default_factory=list)
    salary_negotiation_tips: str = ""
    red_flags_to_watch: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level pipeline output
# ---------------------------------------------------------------------------

class JobMatcherPipelineResult(BaseModel):
    """Final result returned by the orchestrator after the full pipeline."""
    profile: CandidateProfile
    top_matches: List[JobMatch] = Field(default_factory=list)
    customized_resumes: List[CustomizedResume] = Field(default_factory=list)
    cover_letters: List[CoverLetter] = Field(default_factory=list)
    gap_reports: List[GapAnalysisReport] = Field(default_factory=list)
    interview_kits: List[InterviewPrepKit] = Field(default_factory=list)
    agent_trace: List[str] = Field(default_factory=list)
    status: str = "complete"
