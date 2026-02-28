"""
Centralised configuration for the Job Matcher multi-agent system.
Each agent / MCP server imports from here so port assignments and
model names are managed in one place.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", "4096"))

# ---------------------------------------------------------------------------
# Agent ports  (each agent is an independent FastAPI service)
# ---------------------------------------------------------------------------

ORCHESTRATOR_PORT: int = int(os.getenv("ORCHESTRATOR_PORT", "8000"))
PROFILE_PARSER_PORT: int = int(os.getenv("PROFILE_PARSER_PORT", "8001"))
JOB_DISCOVERY_PORT: int = int(os.getenv("JOB_DISCOVERY_PORT", "8002"))
JOB_MATCHER_PORT: int = int(os.getenv("JOB_MATCHER_PORT", "8003"))
RESUME_CUSTOMIZER_PORT: int = int(os.getenv("RESUME_CUSTOMIZER_PORT", "8004"))
COVER_LETTER_PORT: int = int(os.getenv("COVER_LETTER_PORT", "8005"))
GAP_ANALYSIS_PORT: int = int(os.getenv("GAP_ANALYSIS_PORT", "8006"))
INTERVIEW_PREP_PORT: int = int(os.getenv("INTERVIEW_PREP_PORT", "8007"))

# ---------------------------------------------------------------------------
# MCP Server ports
# ---------------------------------------------------------------------------

LINKEDIN_MCP_PORT: int = int(os.getenv("LINKEDIN_MCP_PORT", "9001"))
JOB_BOARDS_MCP_PORT: int = int(os.getenv("JOB_BOARDS_MCP_PORT", "9002"))
DOCUMENT_MCP_PORT: int = int(os.getenv("DOCUMENT_MCP_PORT", "9003"))
MEMORY_MCP_PORT: int = int(os.getenv("MEMORY_MCP_PORT", "9004"))

# ---------------------------------------------------------------------------
# Agent base URLs (used by the A2A client)
# ---------------------------------------------------------------------------

_HOST = os.getenv("AGENT_HOST", "localhost")


def _url(port: int) -> str:
    return f"http://{_HOST}:{port}"


AGENT_URLS: dict[str, str] = {
    "orchestrator":    os.getenv("ORCHESTRATOR_URL",      _url(ORCHESTRATOR_PORT)),
    "profile_parser":  os.getenv("PROFILE_PARSER_URL",    _url(PROFILE_PARSER_PORT)),
    "job_discovery":   os.getenv("JOB_DISCOVERY_URL",     _url(JOB_DISCOVERY_PORT)),
    "job_matcher":     os.getenv("JOB_MATCHER_URL",       _url(JOB_MATCHER_PORT)),
    "resume_customizer": os.getenv("RESUME_CUSTOMIZER_URL", _url(RESUME_CUSTOMIZER_PORT)),
    "cover_letter":    os.getenv("COVER_LETTER_URL",      _url(COVER_LETTER_PORT)),
    "gap_analysis":    os.getenv("GAP_ANALYSIS_URL",      _url(GAP_ANALYSIS_PORT)),
    "interview_prep":  os.getenv("INTERVIEW_PREP_URL",    _url(INTERVIEW_PREP_PORT)),
}

MCP_URLS: dict[str, str] = {
    "linkedin":   os.getenv("LINKEDIN_MCP_URL",   _url(LINKEDIN_MCP_PORT)),
    "job_boards": os.getenv("JOB_BOARDS_MCP_URL", _url(JOB_BOARDS_MCP_PORT)),
    "document":   os.getenv("DOCUMENT_MCP_URL",   _url(DOCUMENT_MCP_PORT)),
    "memory":     os.getenv("MEMORY_MCP_URL",     _url(MEMORY_MCP_PORT)),
}

# ---------------------------------------------------------------------------
# External APIs
# ---------------------------------------------------------------------------

TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
SERPAPI_KEY: str = os.getenv("SERPAPI_KEY", "")          # Alternative job-board scraping
RAPIDAPI_KEY: str = os.getenv("RAPIDAPI_KEY", "")        # Indeed / Glassdoor via RapidAPI

# ---------------------------------------------------------------------------
# Redis (used for shared blackboard state between agents)
# ---------------------------------------------------------------------------

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Vector / Memory
# ---------------------------------------------------------------------------

CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "/tmp/job_matcher_chroma")

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------

MAX_JOBS_TO_DISCOVER: int = int(os.getenv("MAX_JOBS_TO_DISCOVER", "30"))
TOP_N_MATCHES: int = int(os.getenv("TOP_N_MATCHES", "5"))
GENERATE_DOCS_FOR_TOP_N: int = int(os.getenv("GENERATE_DOCS_FOR_TOP_N", "3"))
