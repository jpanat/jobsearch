# Job Matcher — Multi-Agent Architecture

> **Stack:** Python · FastAPI · Claude (Anthropic) · A2A Protocol · MCP Servers · ChromaDB · Docker Compose

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         USER INPUT                                       │
│  LinkedIn URL  ──┐                                                       │
│  Resume PDF    ──┼──▶  Orchestrator Agent  (port 8000)                  │
│  Resume Text   ──┘         │                                             │
└────────────────────────────┼─────────────────────────────────────────────┘
                             │ coordinates via A2A protocol
          ┌──────────────────┼──────────────────────────────────┐
          │                  │                                  │
          ▼                  ▼                                  ▼
  ┌───────────────┐  ┌───────────────┐              ┌─────────────────────┐
  │Profile Parser │  │ Job Discovery │              │  (parallel, per job)│
  │  Agent :8001  │  │  Agent :8002  │              │                     │
  └──────┬────────┘  └──────┬────────┘              │ ┌─────────────────┐ │
         │                  │                        │ │Resume Customizer│ │
         │ CandidateProfile │ JobListings[]          │ │   Agent :8004   │ │
         └──────────────────┘                        │ ├─────────────────┤ │
                  │                                  │ │  Cover Letter   │ │
                  ▼                                  │ │   Agent :8005   │ │
         ┌────────────────┐                          │ ├─────────────────┤ │
         │  Job Matcher   │  JobMatch[] (scored)     │ │  Gap Analysis   │ │
         │  Agent :8003   │──────────────────────────▶ │   Agent :8006   │ │
         └────────────────┘                          │ ├─────────────────┤ │
                                                     │ │ Interview Prep  │ │
                                                     │ │   Agent :8007   │ │
                                                     └─────────────────────┘
                                                              │
                                                              ▼
                                                   JobMatcherPipelineResult
```

---

## Agent Inventory

| Agent | Port | Responsibility | Key A2A Skill |
|---|---|---|---|
| **Orchestrator** | 8000 | Pipeline coordinator; REST API for UI | `run_pipeline` |
| **Profile Parser** | 8001 | Parse LinkedIn URL, resume PDF/text → `CandidateProfile` | `parse_linkedin_profile` |
| **Job Discovery** | 8002 | Fan-out to Indeed, Glassdoor, LinkedIn Jobs | `discover_jobs` |
| **Job Matcher** | 8003 | Score + rank jobs (skills/XP/location/salary) | `score_jobs` |
| **Resume Customizer** | 8004 | Rewrite resume for a specific job, ATS score | `customize_resume` |
| **Cover Letter** | 8005 | Generate tailored cover letter | `generate_cover_letter` |
| **Gap Analysis** | 8006 | Identify skill/XP gaps + learning roadmap | `analyze_gaps` |
| **Interview Prep** | 8007 | Questions, STAR prompts, company research | `prepare_interview` |

---

## MCP Server Inventory

| Server | Port | Tools Exposed |
|---|---|---|
| **LinkedIn MCP** | 9001 | `fetch_linkedin_profile`, `parse_profile_text`, `search_linkedin_people` |
| **Job Boards MCP** | 9002 | `search_indeed`, `search_glassdoor`, `search_linkedin_jobs`, `get_job_details` |
| **Document MCP** | 9003 | `extract_text_from_pdf`, `render_resume_markdown`, `render_cover_letter`, `diff_resumes` |
| **Memory MCP** | 9004 | `upsert_profile`, `upsert_job`, `search_similar_jobs`, `recall_profile`, `recall_job` |

---

## A2A Protocol

Each agent is a self-contained FastAPI service that implements the
[Google A2A spec](https://google.github.io/A2A/).

```
GET  /.well-known/agent.json   →  AgentCard (name, skills, capabilities)
POST /rpc                      →  JSON-RPC 2.0 dispatcher

Methods:
  tasks/send    — submit a new task
  tasks/get     — poll task status
  tasks/cancel  — cancel a running task
```

**Wire format (tasks/send):**
```json
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "<uuid>",
    "sessionId": "<uuid>",
    "message": {
      "role": "user",
      "parts": [{"type": "data", "data": {"skill": "score_jobs", "input": {...}}}]
    }
  }
}
```

Task lifecycle:  `submitted → working → completed | failed`

---

## MCP Protocol

Each MCP server speaks **Streamable HTTP** (JSON-RPC over POST to `/`).

```
POST /   { "method": "initialize" }            →  server capabilities
POST /   { "method": "tools/list" }            →  tool definitions
POST /   { "method": "tools/call",
           "params": { "name": "...", "arguments": {...} } }  →  tool result
GET  /health                                   →  liveness probe
```

---

## Data Flow (step by step)

```
1. User submits LinkedIn URL + optional resume to Orchestrator (POST /run)

2. Orchestrator → [A2A] → Profile Parser
      Input:  linkedin_url, resume_text
      Output: CandidateProfile (skills, experience, education, …)

3. Orchestrator → [A2A] → Job Discovery
      Input:  CandidateProfile
      Internally:
        a. Claude synthesises 4 search queries from profile
        b. Fan-out: 4 queries × 3 boards = 12 parallel searches via Job Boards MCP
        c. De-duplicate by (title, company)
        d. Store each job in Memory MCP (ChromaDB)
      Output: JobListing[] (up to 30)

4. Orchestrator → [A2A] → Job Matcher
      Input:  CandidateProfile, JobListing[]
      Scoring formula:
        overall = 0.40×skills + 0.30×experience + 0.20×location + 0.10×salary
      Output: JobMatch[] ranked by overall_score

5. For each top-N match — ALL IN PARALLEL:
   5a. Resume Customizer → CustomizedResume (ATS-optimised, keywords injected)
   5b. Cover Letter      → CoverLetter (personalised, tone-aware)
   5c. Gap Analysis      → GapAnalysisReport (skill gaps + learning roadmap)

6. For each completed GapAnalysisReport:
   Interview Prep → InterviewPrepKit (8+ questions, STAR prompts, negotiation tips)

7. Orchestrator aggregates and returns JobMatcherPipelineResult
```

---

## Scoring Model

```
Skills Match (40%)
  = (matched_required_skills / total_required) × 100
    + bonus for preferred skills (up to +15)

Experience Match (30%)
  = alignment between candidate YoE and role seniority level
    (e.g., 5 YoE → Senior = 95, 5 YoE → Staff = 60)

Location Match (20%)
  = 100 if remote role + candidate prefers remote
  = 100 if same metro area
  = 0   if incompatible work mode or geography

Salary Match (10%)
  = 100 if job_max >= candidate_expectation
  = (job_max / candidate_expectation) × 100 if below
  = 100 if no salary data (neutral)
```

---

## Project Structure

```
job_matcher/
├── a2a/
│   ├── protocol.py          AgentCard, Task, Message, Part, JSON-RPC types
│   ├── client.py            Async A2A client (send_task, poll, get_artifact)
│   └── server.py            BaseA2AAgent (FastAPI + JSON-RPC dispatcher)
│
├── mcp_servers/
│   ├── linkedin_mcp/        LinkedIn profile fetching + parsing
│   ├── job_boards_mcp/      Indeed / Glassdoor / LinkedIn Jobs search
│   ├── document_mcp/        PDF extraction + Markdown rendering
│   └── memory_mcp/          ChromaDB vector store for profiles + jobs
│
├── agents/
│   ├── orchestrator/        Pipeline coordinator + REST API
│   ├── profile_parser/      LinkedIn URL / PDF / text → CandidateProfile
│   ├── job_discovery/       Multi-board job search + deduplication
│   ├── job_matcher/         Multi-dimension scoring + ranking
│   ├── resume_customizer/   ATS-optimised resume rewriting
│   ├── cover_letter/        Tailored cover letter generation
│   ├── gap_analysis/        Skill gap identification + roadmap
│   └── interview_prep/      Questions, STAR, company research
│
├── shared/
│   ├── models.py            Shared Pydantic types (all agents use these)
│   └── config.py            Ports, API keys, pipeline settings
│
├── .env.example             Template — copy to .env and fill in keys
├── requirements.txt         Python dependencies
├── Dockerfile               Container image (all agents share one image)
└── docker-compose.yml       Spins up all 12 services
```

---

## API Keys Needed

| Key | Purpose | Free tier? |
|---|---|---|
| `ANTHROPIC_API_KEY` | All LLM calls (Claude) | No — pay per token |
| `TAVILY_API_KEY` | Web search fallback + company research | Yes (1k req/mo) |
| `RAPIDAPI_KEY` | JSearch API (Indeed + LinkedIn + Glassdoor) | Yes (200 req/mo) |
| `SERPAPI_KEY` | Alternative job search | Yes (100 req/mo) |

---

## Quick Start

```bash
# 1. Install deps
cd job_matcher
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY at minimum

# 3a. Run with Docker Compose (recommended — all 12 services)
docker-compose up --build

# 3b. OR run locally (12 terminals / tmux panes):
python -m job_matcher.mcp_servers.linkedin_mcp.server   &
python -m job_matcher.mcp_servers.job_boards_mcp.server &
python -m job_matcher.mcp_servers.document_mcp.server   &
python -m job_matcher.mcp_servers.memory_mcp.server     &
python -m job_matcher.agents.profile_parser.agent       &
python -m job_matcher.agents.job_discovery.agent        &
python -m job_matcher.agents.job_matcher.agent          &
python -m job_matcher.agents.resume_customizer.agent    &
python -m job_matcher.agents.cover_letter.agent         &
python -m job_matcher.agents.gap_analysis.agent         &
python -m job_matcher.agents.interview_prep.agent       &
python -m job_matcher.agents.orchestrator.agent

# 4. Trigger a pipeline run
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/yourprofile",
    "remote_ok": true,
    "desired_roles": ["Senior Python Engineer", "Staff Backend Engineer"],
    "salary_expectation_usd": 180000,
    "tone": "professional",
    "top_n": 5
  }'

# 5. Discover registered agents
curl http://localhost:8000/agents
```

---

## Extending the System

**Add a new specialist agent:**
1. Create `agents/my_agent/agent.py` subclassing `BaseA2AAgent`
2. Implement `agent_card()` and `handle_skill()`
3. Add its URL to `shared/config.py → AGENT_URLS`
4. Call it from the Orchestrator with `self._a2a("my_agent", "my_skill", {...})`
5. Add a service block to `docker-compose.yml`

**Add a new MCP tool:**
1. Add the tool definition to the server's `TOOLS` list
2. Implement the async function
3. Register it in `TOOL_MAP`
4. Agents call it via `httpx.AsyncClient` POST to the MCP server

**Swap the job board backend:**
- Set `RAPIDAPI_KEY` for JSearch (covers Indeed + LinkedIn + Glassdoor natively)
- Or implement a Proxycurl adapter in `job_boards_mcp/server.py`

**Scale to production:**
- Replace in-process ChromaDB with Pinecone or Weaviate
- Add Redis for shared task state between Orchestrator instances
- Deploy each agent as a separate Kubernetes Deployment
- Add Prometheus metrics endpoint (`/metrics`) to each agent
