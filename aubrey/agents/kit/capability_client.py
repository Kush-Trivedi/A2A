"""The agent's ONLY line to data and models: aubrey's capability plane.

Auth is the team service token (Bearer); identity of the end user travels
in the envelope payload and is re-enforced by aubrey. LLM output streams
token-by-token; an error event mid-stream raises — never silently ends."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .context_envelope import ContextEnvelope


class CapabilityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _envelope_payload(envelope: ContextEnvelope) -> dict[str, Any]:
    return {
        "user_id": envelope.user_id,
        "actor_id": envelope.actor_id,
        "roles": list(envelope.roles),
        "session_id": envelope.session_id or None,
        "correlation_id": envelope.correlation_id or None,
        "purpose": envelope.purpose,
        "delegated_from": list(envelope.delegated_from),
    }


class AubreyCapabilityClient:
    def __init__(
        self,
        *,
        base_url: str,
        team_token: str,
        agent_key: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._agent_key = agent_key
        self._headers = {"Authorization": f"Bearer {team_token}"}
        self._timeout = timeout_seconds

    async def retrieve(
        self,
        *,
        envelope: ContextEnvelope,
        query: str,
        mode: str | None = None,
        top_k: int | None = None,
        min_similarity: float | None = None,
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/knowledge/retrieve",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "query": query,
                    "mode": mode,
                    "top_k": top_k,
                    "min_similarity": min_similarity,
                },
            )
            self._raise_for_status(response)
            return list(response.json()["data"]["chunks"])

    async def llm_chat_stream(
        self,
        *,
        envelope: ContextEnvelope,
        messages: list[dict[str, str]],
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/v1/capability/llm/chat/stream",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "messages": messages,
                    "max_output_tokens": max_output_tokens,
                },
            ) as response:
                if response.status_code >= 400:
                    await response.aread()  # body needed before parsing the error
                    self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line[len("data: "):])
                    if payload.get("done"):
                        return
                    if "error" in payload:
                        error = payload["error"]
                        raise CapabilityError(
                            str(error.get("code") or "llm_stream_failed"),
                            str(error.get("message") or "LLM stream failed."),
                        )
                    text = payload.get("text")
                    if text:
                        yield text

    async def data_genie(
        self, *, envelope: ContextEnvelope, connection_key: str, question: str
    ) -> dict[str, Any]:
        """Natural-language answer from the team's Genie connection —
        {text, sql, columns, rows, row_count, truncated, warnings}."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/data/genie",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "connection_key": connection_key,
                    "question": question,
                },
            )
            self._raise_for_status(response)
            return dict(response.json()["data"])

    async def data_sql(
        self, *, envelope: ContextEnvelope, connection_key: str, statement: str
    ) -> dict[str, Any]:
        """Direct SQL on the team's warehouse connection — the fast lane."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/data/sql",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "connection_key": connection_key,
                    "statement": statement,
                },
            )
            self._raise_for_status(response)
            return dict(response.json()["data"])

    async def session_documents(
        self, *, envelope: ContextEnvelope
    ) -> list[dict[str, Any]]:
        """Documents the user uploaded into this conversation (envelope's
        session) — the file agent's only knowledge source."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/files/context",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                },
            )
            self._raise_for_status(response)
            return list(response.json()["data"]["documents"])

    async def accessible_agents(
        self, *, envelope: ContextEnvelope
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/agents/catalog",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                },
            )
            self._raise_for_status(response)
            return list(response.json()["data"]["agents"])

    async def register_self(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/agents/register",
                headers=self._headers,
                json=payload,
            )
            self._raise_for_status(response)
            return dict(response.json().get("data") or {})

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 — non-JSON error body
            body = {}
        error = body.get("error") or {}
        raise CapabilityError(
            str(error.get("code") or f"http_{response.status_code}"),
            str(body.get("message") or response.reason_phrase or "capability call failed"),
        )
