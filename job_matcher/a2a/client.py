"""
A2A Client — used by any agent to call another agent or the orchestrator.

Usage
-----
    from job_matcher.a2a.client import A2AClient
    from job_matcher.shared.config import AGENT_URLS

    client = A2AClient(AGENT_URLS["profile_parser"])
    result = await client.send_task(
        skill_id="parse_linkedin_profile",
        input_data={"linkedin_url": "https://linkedin.com/in/johndoe"},
    )
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx

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

# How long to poll before giving up (seconds)
DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 1.0


class A2AError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class A2AClient:
    """
    Thin async HTTP client that speaks the A2A JSON-RPC protocol.
    One instance per remote agent URL.
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def get_agent_card(self) -> AgentCard:
        """Fetch the remote agent's self-description."""
        url = f"{self.base_url}/.well-known/agent.json"
        resp = await self._http.get(url)
        resp.raise_for_status()
        return AgentCard(**resp.json())

    # ------------------------------------------------------------------
    # Core RPC call
    # ------------------------------------------------------------------

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        """Send a JSON-RPC 2.0 request and return the result field."""
        req = JsonRpcRequest(method=method, params=params)
        resp = await self._http.post(
            f"{self.base_url}/rpc",
            content=req.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        rpc_resp = JsonRpcResponse(**resp.json())

        if rpc_resp.error:
            raise A2AError(
                code=rpc_resp.error.code,
                message=rpc_resp.error.message,
                data=rpc_resp.error.data,
            )
        return rpc_resp.result

    # ------------------------------------------------------------------
    # Task API
    # ------------------------------------------------------------------

    async def send_task(
        self,
        skill_id: str,
        input_data: Dict[str, Any],
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Submit a task to the remote agent and poll until it completes.

        Returns the completed Task object with results in task.artifacts.
        """
        session_id = session_id or str(uuid4())

        # Build the initial user message
        user_msg = Message.data(
            role="user",
            payload={"skill": skill_id, "input": input_data},
        )

        params = {
            "id": str(uuid4()),
            "sessionId": session_id,
            "message": user_msg.model_dump(),
            "metadata": metadata or {},
        }

        result = await self._rpc(A2AMethods.SEND_TASK, params)
        task = Task(**result)

        # Poll until terminal state
        task = await self._wait_for_completion(task.id, session_id)
        return task

    async def get_task(self, task_id: str, session_id: str) -> Task:
        result = await self._rpc(
            A2AMethods.GET_TASK,
            {"id": task_id, "sessionId": session_id},
        )
        return Task(**result)

    async def cancel_task(self, task_id: str, session_id: str) -> Task:
        result = await self._rpc(
            A2AMethods.CANCEL_TASK,
            {"id": task_id, "sessionId": session_id},
        )
        return Task(**result)

    # ------------------------------------------------------------------
    # Polling helper
    # ------------------------------------------------------------------

    async def _wait_for_completion(self, task_id: str, session_id: str) -> Task:
        terminal = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}
        elapsed = 0.0

        while elapsed < self.timeout:
            task = await self.get_task(task_id, session_id)
            logger.debug("Task %s state=%s", task_id, task.status)

            if task.status in terminal:
                if task.status == TaskState.FAILED:
                    # Surface any error message from the task
                    error_msgs = [
                        getattr(part, "text", "")
                        for msg in task.messages
                        if msg.role == "agent"
                        for part in (msg.parts if isinstance(msg.parts, list) else [])
                    ]
                    raise A2AError(
                        code=-1,
                        message=f"Task failed: {' '.join(error_msgs)}",
                    )
                return task

            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

        raise TimeoutError(f"Task {task_id} did not complete within {self.timeout}s")

    # ------------------------------------------------------------------
    # Convenience: extract typed artifact
    # ------------------------------------------------------------------

    @staticmethod
    def get_artifact(task: Task, name: str) -> Any:
        """
        Return the data payload of the first artifact with the given name,
        or None if not found.
        """
        for artifact in task.artifacts:
            if artifact.get("name") == name:
                return artifact.get("data")
        return None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "A2AClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
