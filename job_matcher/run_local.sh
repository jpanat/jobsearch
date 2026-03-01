#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_local.sh  — start all 12 services locally (no Docker needed)
# Usage: bash job_matcher/run_local.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_ROOT/job_matcher/.env"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  echo ""
  echo "  ERROR: $ENV_FILE not found."
  echo "  Run first:"
  echo "    cp job_matcher/.env.example job_matcher/.env"
  echo "    # then add your ANTHROPIC_API_KEY to job_matcher/.env"
  echo ""
  exit 1
fi

# Load env vars so we can read port numbers
set -o allexport
source "$ENV_FILE"
set +o allexport

ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8000}"
PROFILE_PARSER_PORT="${PROFILE_PARSER_PORT:-8001}"
JOB_DISCOVERY_PORT="${JOB_DISCOVERY_PORT:-8002}"
JOB_MATCHER_PORT="${JOB_MATCHER_PORT:-8003}"
RESUME_CUSTOMIZER_PORT="${RESUME_CUSTOMIZER_PORT:-8004}"
COVER_LETTER_PORT="${COVER_LETTER_PORT:-8005}"
GAP_ANALYSIS_PORT="${GAP_ANALYSIS_PORT:-8006}"
INTERVIEW_PREP_PORT="${INTERVIEW_PREP_PORT:-8007}"
LINKEDIN_MCP_PORT="${LINKEDIN_MCP_PORT:-9001}"
JOB_BOARDS_MCP_PORT="${JOB_BOARDS_MCP_PORT:-9002}"
DOCUMENT_MCP_PORT="${DOCUMENT_MCP_PORT:-9003}"
MEMORY_MCP_PORT="${MEMORY_MCP_PORT:-9004}"

LOG_DIR="$REPO_ROOT/job_matcher/.logs"
mkdir -p "$LOG_DIR"

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT"

echo "========================================================"
echo " Job Matcher — starting 12 services"
echo "========================================================"

start_service() {
  local name="$1"
  local module="$2"
  local port="$3"
  echo "  Starting $name on :$port ..."
  PYTHONPATH="$REPO_ROOT" python3 -m "$module" \
    > "$LOG_DIR/${name}.log" 2>&1 &
  echo $! >> "$LOG_DIR/pids.txt"
}

# Cleanup on Ctrl-C
cleanup() {
  echo ""
  echo "Stopping all services..."
  if [ -f "$LOG_DIR/pids.txt" ]; then
    while read -r pid; do
      kill "$pid" 2>/dev/null || true
    done < "$LOG_DIR/pids.txt"
    rm "$LOG_DIR/pids.txt"
  fi
  echo "Done."
}
trap cleanup INT TERM

# Clear old PIDs
rm -f "$LOG_DIR/pids.txt"

# ── MCP Servers (start first) ─────────────────────────────────────────────────
start_service "linkedin_mcp"   "job_matcher.mcp_servers.linkedin_mcp.server"   "$LINKEDIN_MCP_PORT"
start_service "job_boards_mcp" "job_matcher.mcp_servers.job_boards_mcp.server" "$JOB_BOARDS_MCP_PORT"
start_service "document_mcp"   "job_matcher.mcp_servers.document_mcp.server"   "$DOCUMENT_MCP_PORT"
start_service "memory_mcp"     "job_matcher.mcp_servers.memory_mcp.server"     "$MEMORY_MCP_PORT"

echo "  Waiting 3s for MCP servers to boot..."
sleep 3

# ── Specialist Agents ─────────────────────────────────────────────────────────
start_service "profile_parser"    "job_matcher.agents.profile_parser.agent"    "$PROFILE_PARSER_PORT"
start_service "job_discovery"     "job_matcher.agents.job_discovery.agent"     "$JOB_DISCOVERY_PORT"
start_service "job_matcher_agent" "job_matcher.agents.job_matcher.agent"       "$JOB_MATCHER_PORT"
start_service "resume_customizer" "job_matcher.agents.resume_customizer.agent" "$RESUME_CUSTOMIZER_PORT"
start_service "cover_letter"      "job_matcher.agents.cover_letter.agent"      "$COVER_LETTER_PORT"
start_service "gap_analysis"      "job_matcher.agents.gap_analysis.agent"      "$GAP_ANALYSIS_PORT"
start_service "interview_prep"    "job_matcher.agents.interview_prep.agent"    "$INTERVIEW_PREP_PORT"

echo "  Waiting 3s for agents to boot..."
sleep 3

# ── Orchestrator (last) ───────────────────────────────────────────────────────
start_service "orchestrator" "job_matcher.agents.orchestrator.agent" "$ORCHESTRATOR_PORT"

sleep 2

echo ""
echo "========================================================"
echo " All services running. Logs in: $LOG_DIR/"
echo ""
echo " Orchestrator API:  http://localhost:${ORCHESTRATOR_PORT}"
echo " Agent registry:    http://localhost:${ORCHESTRATOR_PORT}/agents"
echo ""
echo " Example request:"
echo "   curl -s -X POST http://localhost:${ORCHESTRATOR_PORT}/run \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"linkedin_url\": \"https://linkedin.com/in/yourprofile\", \"remote_ok\": true}'"
echo ""
echo " Press Ctrl-C to stop all services."
echo "========================================================"

# Keep script alive
wait
