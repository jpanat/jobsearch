"""
A2A Server base class.

Every specialist agent inherits from BaseA2AAgent and only needs to:
  1. Define its AgentCard (name, description, skills).
  2. Implement handle_skill(skill_id, input_data, task) -> dict

The base class wires up:
  • GET  /.well-known/agent.json   → AgentCard
  • POST /rpc                      → JSON-RPC 2.0 dispatcher
  • In-memory task store (swap for Redis in production)
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from job_matcher.a2a.protocol import (
    A2AMethods,
    AgentCard,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    Message,
    Task,
    TaskState,
)

logger = logging.getLogger(__name__)


class BaseA2AAgent(ABC):
    """
    Abstract base for all Job Matcher agents.

    Subclass and implement:
        agent_card() -> AgentCard
        handle_skill(skill_id, input_data, task) -> dict   (the artifact payload)
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}           # task_id → Task
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def agent_card(self) -> AgentCard:  # pragma: no cover
        ...

    @abstractmethod
    async def handle_skill(
        self,
        skill_id: str,
        input_data: Dict[str, Any],
        task: Task,
    ) -> Dict[str, Any]:  # pragma: no cover
        """
        Execute the requested skill and return a dict that will be stored
        as an artifact named after skill_id.
        """
        ...

    # ------------------------------------------------------------------
    # FastAPI wiring
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title=self.__class__.__name__)

        @app.get("/.well-known/agent.json")
        async def agent_card_endpoint():
            return self.agent_card().model_dump()

        @app.post("/rpc")
        async def rpc_endpoint(request: Request):
            body = await request.json()
            return await self._dispatch(body)

        @app.get("/health")
        async def health():
            return {"status": "ok", "agent": self.agent_card().name}

        return app

    # ------------------------------------------------------------------
    # JSON-RPC dispatcher
    # ------------------------------------------------------------------

    async def _dispatch(self, body: Dict[str, Any]) -> JSONResponse:
        try:
            rpc_req = JsonRpcRequest(**body)
        except Exception as exc:
            error = JsonRpcError(code=-32600, message=f"Invalid request: {exc}")
            return JSONResponse(
                JsonRpcResponse(id="", error=error).model_dump()
            )

        try:
            if rpc_req.method == A2AMethods.SEND_TASK:
                result = await self._handle_send_task(rpc_req.params)
            elif rpc_req.method == A2AMethods.GET_TASK:
                result = await self._handle_get_task(rpc_req.params)
            elif rpc_req.method == A2AMethods.CANCEL_TASK:
                result = await self._handle_cancel_task(rpc_req.params)
            else:
                raise ValueError(f"Unknown method: {rpc_req.method}")

            return JSONResponse(
                JsonRpcResponse(id=rpc_req.id, result=result).model_dump()
            )

        except Exception as exc:
            logger.exception("RPC error for method %s", rpc_req.method)
            error = JsonRpcError(code=-32000, message=str(exc))
            return JSONResponse(
                JsonRpcResponse(id=rpc_req.id, error=error).model_dump()
            )

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    async def _handle_send_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("id", str(uuid4()))
        session_id = params.get("sessionId", str(uuid4()))
        raw_message = params.get("message", {})
        metadata = params.get("metadata", {})

        # Parse the incoming message
        msg = Message(**raw_message)

        task = Task(
            id=task_id,
            session_id=session_id,
            status=TaskState.WORKING,
            messages=[msg],
            metadata=metadata,
        )
        self._tasks[task_id] = task

        # Extract skill + input from the DataPart
        skill_id, input_data = self._extract_skill(msg)

        # Run the skill asynchronously and update task when done
        asyncio.create_task(self._run_skill(task, skill_id, input_data))

        return task.model_dump()

    async def _handle_get_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("id", "")
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task.model_dump()

    async def _handle_cancel_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("id", "")
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        task.status = TaskState.CANCELED
        task.touch()
        return task.model_dump()

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    async def _run_skill(
        self,
        task: Task,
        skill_id: str,
        input_data: Dict[str, Any],
    ) -> None:
        try:
            result_data = await self.handle_skill(skill_id, input_data, task)
            task.artifacts.append({"name": skill_id, "data": result_data})
            task.status = TaskState.COMPLETED
            task.messages.append(
                Message.text(role="agent", text=f"Skill '{skill_id}' completed.")
            )
        except Exception as exc:
            logger.exception("Skill '%s' failed", skill_id)
            task.status = TaskState.FAILED
            task.messages.append(
                Message.text(role="agent", text=f"Error: {exc}")
            )
        finally:
            task.touch()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_skill(msg: Message):
        """Pull skill_id and input_data out of a DataPart in the message."""
        for part in msg.parts:
            if hasattr(part, "data"):
                payload = part.data
                return payload.get("skill", "unknown"), payload.get("input", {})
        raise ValueError("No DataPart found in message — cannot extract skill")
