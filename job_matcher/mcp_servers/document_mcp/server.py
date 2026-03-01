"""
Document MCP Server
────────────────────
Tools for reading and generating candidate documents.

Tools exposed:
  • extract_text_from_pdf   — extract plain text from an uploaded PDF resume
  • render_resume_markdown  — convert CustomizedResume JSON → polished Markdown
  • render_cover_letter     — convert CoverLetter JSON → polished Markdown
  • diff_resumes            — highlight differences between two resume texts
"""

from __future__ import annotations

import base64
import json
import logging
import re
import textwrap

from anthropic import AsyncAnthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from job_matcher.shared.config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MODEL,
    DOCUMENT_MCP_PORT,
)

logger = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "extract_text_from_pdf",
        "description": "Extract plain text from a base64-encoded PDF resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdf_b64": {
                    "type": "string",
                    "description": "Base64-encoded PDF file content.",
                }
            },
            "required": ["pdf_b64"],
        },
    },
    {
        "name": "render_resume_markdown",
        "description": "Convert a CustomizedResume JSON object to polished Markdown suitable for display or conversion to PDF.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resume_json": {
                    "type": "object",
                    "description": "CustomizedResume data as returned by the resume_customizer agent.",
                }
            },
            "required": ["resume_json"],
        },
    },
    {
        "name": "render_cover_letter",
        "description": "Convert a CoverLetter JSON object to a polished Markdown cover letter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cover_letter_json": {
                    "type": "object",
                    "description": "CoverLetter data as returned by the cover_letter agent.",
                }
            },
            "required": ["cover_letter_json"],
        },
    },
    {
        "name": "diff_resumes",
        "description": "Highlight differences between an original resume text and a customised version.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "original": {"type": "string", "description": "Original resume text."},
                "customized": {"type": "string", "description": "Customized resume text."},
            },
            "required": ["original", "customized"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def extract_text_from_pdf(pdf_b64: str) -> dict:
    """
    Decode a base64 PDF and extract text.
    Uses PyMuPDF (fitz) if available; falls back to Claude vision.
    """
    try:
        import fitz  # PyMuPDF
        raw_bytes = base64.b64decode(pdf_b64)
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        return {"text": text, "pages": len(doc), "method": "pymupdf"}
    except ImportError:
        pass

    # Fallback: send to Claude with vision
    response = await client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": "Extract all text from this PDF resume. Return only the raw text."},
            ],
        }],
    )
    return {"text": response.content[0].text, "pages": None, "method": "claude_vision"}


async def render_resume_markdown(resume_json: dict) -> dict:
    name = resume_json.get("candidate_name", "Candidate")
    title = resume_json.get("target_title", "")
    summary = resume_json.get("summary", "")
    sections = resume_json.get("sections", [])
    keywords = resume_json.get("keywords_added", [])
    ats_score = resume_json.get("ats_score_estimate", 0)

    lines = [
        f"# {name}",
        f"**{title}**\n",
        "---\n",
        "## Professional Summary",
        summary,
        "",
    ]
    for section in sections:
        lines.append(f"## {section['heading']}")
        lines.append(section["content"])
        lines.append("")

    if keywords:
        lines.append("## Key Skills")
        lines.append(", ".join(keywords))
        lines.append("")

    lines.append(f"\n---\n*ATS Compatibility Score: {ats_score:.0f}/100*")

    return {"markdown": "\n".join(lines)}


async def render_cover_letter(cover_letter_json: dict) -> dict:
    name = cover_letter_json.get("candidate_name", "")
    company = cover_letter_json.get("company_name", "")
    hiring_mgr = cover_letter_json.get("hiring_manager", "Hiring Manager")
    subject = cover_letter_json.get("subject_line", "")
    body = cover_letter_json.get("body", "")
    cta = cover_letter_json.get("call_to_action", "")

    md = textwrap.dedent(f"""
        **{name}**

        ---

        **Subject:** {subject}

        Dear {hiring_mgr},

        {body}

        {cta}

        Sincerely,
        **{name}**
    """).strip()

    return {"markdown": md}


async def diff_resumes(original: str, customized: str) -> dict:
    response = await client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": (
                "Compare the original and customized resume texts below. "
                "List the key changes made: what was added, removed, or rephrased. "
                "Be concise and specific.\n\n"
                f"ORIGINAL:\n{original[:3000]}\n\n"
                f"CUSTOMIZED:\n{customized[:3000]}"
            ),
        }],
    )
    return {"diff_summary": response.content[0].text}


# ---------------------------------------------------------------------------
# MCP HTTP Server
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "extract_text_from_pdf": extract_text_from_pdf,
    "render_resume_markdown": render_resume_markdown,
    "render_cover_letter": render_cover_letter,
    "diff_resumes": diff_resumes,
}

app = FastAPI(title="Document MCP Server")


@app.get("/health")
async def health():
    return {"status": "ok", "server": "document_mcp"}


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
                "serverInfo": {"name": "document_mcp", "version": "1.0.0"},
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
    uvicorn.run(app, host="0.0.0.0", port=DOCUMENT_MCP_PORT, log_level="info")
