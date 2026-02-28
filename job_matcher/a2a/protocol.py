"""
A2A (Agent-to-Agent) Protocol implementation.

Follows the Google A2A open specification:
  https://google.github.io/A2A/

Key concepts
────────────
AgentCard        Discoverable metadata at /.well-known/agent.json
Task             Unit of work delegated from one agent to another
Message          A single turn in a task conversation
Part             Content atom inside a message (text / data / file)
TaskState        Lifecycle of a task: submitted → working → completed | failed
"""

from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


# ---------------------------------------------------------------------------
# Content parts
# ---------------------------------------------------------------------------

class TextPart(BaseModel):
    type: str = "text"
    text: str


class DataPart(BaseModel):
    """Structured JSON payload."""
    type: str = "data"
    data: Dict[str, Any]


class FilePart(BaseModel):
    """Base64-encoded file content."""
    type: str = "file"
    mime_type: str
    content_b64: str
    filename: Optional[str] = None


Part = Union[TextPart, DataPart, FilePart]


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str                                # "user" | "agent"
    parts: List[Part]
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def text(cls, role: str, text: str) -> "Message":
        return cls(role=role, parts=[TextPart(text=text)])

    @classmethod
    def data(cls, role: str, payload: Dict[str, Any]) -> "Message":
        return cls(role=role, parts=[DataPart(data=payload)])


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: Optional[str] = None
    status: TaskState = TaskState.SUBMITTED
    messages: List[Message] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# AgentCard — served at /.well-known/agent.json
# ---------------------------------------------------------------------------

class AgentCapabilities(BaseModel):
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = True


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """
    Self-description that every A2A agent publishes at
    GET /.well-known/agent.json
    """
    name: str
    description: str
    url: str                                 # Base URL of this agent
    version: str = "1.0.0"
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    skills: List[AgentSkill] = Field(default_factory=list)
    default_input_modes: List[str] = Field(default_factory=lambda: ["text", "data"])
    default_output_modes: List[str] = Field(default_factory=lambda: ["text", "data"])


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 envelope (used on the wire)
# ---------------------------------------------------------------------------

class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str = Field(default_factory=lambda: str(uuid4()))
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)


class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    result: Optional[Any] = None
    error: Optional[JsonRpcError] = None


# ---------------------------------------------------------------------------
# Standard A2A RPC method names
# ---------------------------------------------------------------------------

class A2AMethods:
    SEND_TASK = "tasks/send"
    GET_TASK = "tasks/get"
    CANCEL_TASK = "tasks/cancel"
    SEND_SUBSCRIBE = "tasks/sendSubscribe"   # streaming variant
