"""
Memory MCP Server
──────────────────
Vector-based semantic memory for the job matcher pipeline.
Stores embeddings of candidate profiles and job listings so agents can
perform similarity search without re-fetching data.

Tools exposed:
  • upsert_profile      — embed + store a candidate profile
  • upsert_job          — embed + store a job listing
  • search_similar_jobs — find jobs semantically similar to a profile or query
  • recall_profile      — retrieve a stored candidate profile by id
  • recall_job          — retrieve a stored job by job_id

Backend: ChromaDB (in-process, file-persisted).
Swap for Pinecone / Weaviate in production by replacing the ChromaDB calls.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    CHROMA_PERSIST_DIR,
    MEMORY_MCP_PORT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ChromaDB setup
# ---------------------------------------------------------------------------

_chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)

# Use Anthropic embeddings via a simple adapter, or fall back to sentence-transformers
try:
    _embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
except Exception:
    _embed_fn = embedding_functions.DefaultEmbeddingFunction()

_profiles_col = _chroma_client.get_or_create_collection(
    "candidate_profiles", embedding_function=_embed_fn
)
_jobs_col = _chroma_client.get_or_create_collection(
    "job_listings", embedding_function=_embed_fn
)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "upsert_profile",
        "description": "Store a candidate profile in the vector store for later retrieval and similarity search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string", "description": "Unique ID for this candidate (e.g. LinkedIn URL or UUID)."},
                "profile_json": {"type": "object", "description": "Full CandidateProfile JSON object."},
            },
            "required": ["profile_id", "profile_json"],
        },
    },
    {
        "name": "upsert_job",
        "description": "Store a job listing in the vector store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_json": {"type": "object", "description": "Full JobListing JSON object (must include job_id)."},
            },
            "required": ["job_json"],
        },
    },
    {
        "name": "search_similar_jobs",
        "description": "Find job listings semantically similar to a query string or candidate profile summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description of what to search for."},
                "n_results": {"type": "integer", "default": 10},
                "source_filter": {
                    "type": "string",
                    "description": "Optionally filter by source: 'indeed' | 'glassdoor' | 'linkedin'",
                    "default": "",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "recall_profile",
        "description": "Retrieve a previously stored candidate profile by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "recall_job",
        "description": "Retrieve a previously stored job listing by its job_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _profile_to_text(p: dict) -> str:
    """Create a single text string suitable for embedding from a profile."""
    parts = [
        p.get("headline", ""),
        p.get("summary", ""),
        "Skills: " + ", ".join(p.get("skills", [])),
    ]
    for exp in p.get("experience", []):
        parts.append(f"{exp.get('title','')} at {exp.get('company','')}: {exp.get('description','')}")
    return "\n".join(filter(None, parts))


def _job_to_text(j: dict) -> str:
    """Create embedding text from a job listing."""
    return "\n".join(filter(None, [
        j.get("title", ""),
        j.get("description", ""),
        "Required skills: " + ", ".join(j.get("required_skills", [])),
        "Preferred skills: " + ", ".join(j.get("preferred_skills", [])),
    ]))


async def upsert_profile(profile_id: str, profile_json: dict) -> dict:
    text = _profile_to_text(profile_json)
    _profiles_col.upsert(
        ids=[profile_id],
        documents=[text],
        metadatas=[{"profile_id": profile_id, "name": profile_json.get("full_name", "")}],
    )
    return {"stored": True, "profile_id": profile_id}


async def upsert_job(job_json: dict) -> dict:
    job_id = job_json.get("job_id", str(uuid.uuid4()))
    text = _job_to_text(job_json)
    _jobs_col.upsert(
        ids=[job_id],
        documents=[text],
        metadatas=[{
            "job_id": job_id,
            "title": job_json.get("title", ""),
            "company": job_json.get("company", ""),
            "source": job_json.get("source", ""),
            "job_json": json.dumps(job_json),
        }],
    )
    return {"stored": True, "job_id": job_id}


async def search_similar_jobs(query: str, n_results: int = 10, source_filter: str = "") -> dict:
    where = {"source": source_filter} if source_filter else None
    results = _jobs_col.query(
        query_texts=[query],
        n_results=min(n_results, _jobs_col.count() or 1),
        where=where,
    )
    jobs = []
    for meta, dist in zip(
        results.get("metadatas", [[]])[0],
        results.get("distances", [[]])[0],
    ):
        job_json_str = meta.get("job_json", "{}")
        try:
            job = json.loads(job_json_str)
        except Exception:
            job = {k: v for k, v in meta.items() if k != "job_json"}
        job["similarity_score"] = round(1 - dist, 4)  # cosine distance → score
        jobs.append(job)
    return {"jobs": jobs, "query": query, "total": len(jobs)}


async def recall_profile(profile_id: str) -> dict:
    results = _profiles_col.get(ids=[profile_id])
    if not results["ids"]:
        return {"found": False, "profile_id": profile_id}
    return {"found": True, "profile_id": profile_id, "metadata": results["metadatas"][0]}


async def recall_job(job_id: str) -> dict:
    results = _jobs_col.get(ids=[job_id])
    if not results["ids"]:
        return {"found": False, "job_id": job_id}
    meta = results["metadatas"][0]
    job_json_str = meta.get("job_json", "{}")
    try:
        job = json.loads(job_json_str)
    except Exception:
        job = meta
    return {"found": True, "job": job}


# ---------------------------------------------------------------------------
# MCP HTTP Server
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "upsert_profile": upsert_profile,
    "upsert_job": upsert_job,
    "search_similar_jobs": search_similar_jobs,
    "recall_profile": recall_profile,
    "recall_job": recall_job,
}

app = FastAPI(title="Memory MCP Server")


@app.get("/health")
async def health():
    return {"status": "ok", "server": "memory_mcp"}


@app.post("/")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method", "")
    rpc_id = body.get("id")

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memory_mcp", "version": "1.0.0"},
            },
        })

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": TOOLS}})

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        fn = TOOL_MAP.get(tool_name)
        if fn is None:
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
            })
        try:
            result = await fn(**arguments)
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False},
            })
        except Exception as exc:
            logger.exception("Tool '%s' raised", tool_name)
            return JSONResponse({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            })

    return JSONResponse({
        "jsonrpc": "2.0", "id": rpc_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=MEMORY_MCP_PORT, log_level="info")
