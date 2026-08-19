"""The agent's ONLY line to data and models: aubrey's capability plane.

Auth is the team service token (Bearer); identity of the end user travels
in the envelope payload and is re-enforced by aubrey. LLM output streams
token-by-token; an error event mid-stream raises — never silently ends."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .context_envelope import ENVELOPE_NAMESPACE, ContextEnvelope
from .executor import current_task_id
from .peer_client import stream_peer_text


class CapabilityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _envelope_payload(envelope: ContextEnvelope) -> dict[str, Any]:
    # sig/issued_at ride VERBATIM (M10-S3): aubrey signed these identity
    # fields at dispatch, and capability endpoints verify them when signing
    # is enabled. Empty strings on platforms that predate signing.
    return {
        "user_id": envelope.user_id,
        "actor_id": envelope.actor_id,
        "roles": list(envelope.roles),
        "session_id": envelope.session_id or None,
        "correlation_id": envelope.correlation_id or None,
        "purpose": envelope.purpose,
        "delegated_from": list(envelope.delegated_from),
        "sig": envelope.sig,
        "issued_at": envelope.issued_at,
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

    async def data_ask(
        self,
        *,
        envelope: ContextEnvelope,
        connection_key: str,
        question: str,
        examples: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Fast text-to-SQL over the team's warehouse connection. Returns
        {answerable, reason, sql, columns, rows, ...} — check `answerable`
        before narrating rows."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/data/ask",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "connection_key": connection_key,
                    "question": question,
                    "examples": list(examples or []),
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

    async def mcp_tools(
        self, *, envelope: ContextEnvelope, connection_key: str
    ) -> list[dict[str, Any]]:
        """Vendor tools on the team's MCP connection — [{name, description,
        input_schema}]. Credentials never reach the agent."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/mcp/tools",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "connection_key": connection_key,
                },
            )
            self._raise_for_status(response)
            return list(response.json()["data"])

    async def mcp_call(
        self, *, envelope: ContextEnvelope, connection_key: str,
        tool: str, arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a vendor MCP tool through the platform gateway —
        {is_error, text, structured}. Audited server-side."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/mcp/call",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "connection_key": connection_key,
                    "tool": tool,
                    "arguments": dict(arguments or {}),
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

    async def resolve_peer(
        self, *, envelope: ContextEnvelope, peer_key: str
    ) -> dict[str, Any]:
        """M5 delegation handshake. Aubrey authorizes the hop (peer active +
        permitted to the END USER, caller's declared peer per the yaml
        mirror of the manifest, depth cap, cycle rejection) and returns
        {peer_key, display_name, card_url, envelope} where `envelope` is a
        FRESH platform-signed metadata block with delegated_from extended
        server-side — the chain is signature-covered, so agents cannot
        extend it themselves."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/capability/agents/resolve",
                headers=self._headers,
                json={
                    "envelope": _envelope_payload(envelope),
                    "agent_key": self._agent_key,
                    "peer_key": peer_key,
                },
            )
            self._raise_for_status(response)
            return dict(response.json()["data"])

    def _peer_metadata(
        self, envelope: ContextEnvelope, signed_envelope: dict[str, Any]
    ) -> dict[str, Any]:
        """The metadata block the peer receives: the platform-signed
        identity verbatim, plus this agent's local conversation context
        (window/memory are NOT signature-covered, so adding them is safe)."""
        payload = dict(signed_envelope)
        payload.setdefault("window", [dict(w) for w in envelope.window])
        payload.setdefault("memory", dict(envelope.memory))
        return {ENVELOPE_NAMESPACE: payload}

    async def delegate_stream(
        self,
        *,
        envelope: ContextEnvelope,
        peer_key: str,
        question: str,
        task_id: str | None = None,
    ) -> tuple[str, AsyncIterator[str]]:
        """Resolve first, then stream — returns (peer_key, async_iterator)
        so the caller can attribute BEFORE the first chunk arrives. How an
        agent streams a peer's answer with attribution (display_name comes
        from resolve_peer if you want the human-readable name):

            resolved = await client.resolve_peer(envelope=env, peer_key="benefit")
            peer_key, chunks = await client.delegate_stream(
                envelope=env, peer_key="benefit", question=question)
            yield f"[{resolved['display_name']}] "
            async for chunk in chunks:
                yield chunk

        `task_id` defaults to the executing turn's A2A task id (from the
        kit executor's contextvar) and rides as referenceTaskIds on the
        peer message — the lineage of the consultation."""
        resolved = await self.resolve_peer(envelope=envelope, peer_key=peer_key)
        signed = dict(resolved.get("envelope") or {})
        reference = task_id if task_id is not None else current_task_id()
        iterator = stream_peer_text(
            card_url=str(resolved.get("card_url") or ""),
            text=question,
            context_id=envelope.session_id or envelope.correlation_id,
            metadata=self._peer_metadata(envelope, signed),
            reference_task_ids=(reference,) if reference else (),
            timeout_seconds=self._timeout,
        )
        return str(resolved.get("peer_key") or peer_key), iterator

    async def delegate(
        self,
        *,
        envelope: ContextEnvelope,
        peer_key: str,
        question: str,
        task_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Consult a peer agent and yield its text chunks. Resolution
        (authorization + signed chain extension) happens on first
        iteration; prefix attribution is the caller's choice — use
        delegate_stream when you need the peer identity before streaming."""
        _, chunks = await self.delegate_stream(
            envelope=envelope, peer_key=peer_key, question=question, task_id=task_id
        )
        async for chunk in chunks:
            yield chunk

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
