from dataclasses import dataclass
import json
from typing import Any

import httpx

from .context_envelope import ContextEnvelope
from .delegator import BearerTokenProvider


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    source_name: str
    knowledge_source: str
    content: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AccessibleAgent:
    id: str
    display_name: str
    description: str
    team_key: str
    is_remote: bool


@dataclass(frozen=True)
class ResolvedAgent:
    """Dynamic peer resolution result — card_url is present only when the
    END USER's roles may access the peer (ACE enforces Casbin on resolve)."""

    found: bool
    accessible: bool = False
    agent_key: str = ""
    display_name: str = ""
    team_key: str = ""
    card_url: str = ""
    auth_audience: str = ""


class AceCapabilityClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 60.0,
        bearer_token_provider: BearerTokenProvider | None = None,
        ace_audience: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._token_provider = bearer_token_provider
        self._ace_audience = ace_audience

    async def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token_provider is not None and self._ace_audience:
            token = await self._token_provider(self._ace_audience)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds
        ) as client:
            response = await client.post(path, json=payload, headers=await self._headers())
            response.raise_for_status()
            return response.json()["data"]

    async def retrieve(
        self,
        *,
        envelope: ContextEnvelope,
        query: str,
        knowledge_sources: tuple[str, ...] = (),
        session_id: str | None = None,
        top_k: int | None = None,
        retrieval_mode: str | None = None,
        agent_key: str = "",
    ) -> list[RetrievedChunk]:
        data = await self._post(
            "/api/v1/capability/knowledge/retrieve",
            {
                "envelope": envelope.to_payload(),
                "query": query,
                "knowledge_sources": list(knowledge_sources),
                "session_id": session_id,
                "top_k": top_k,
                "retrieval_mode": retrieval_mode,
                "agent_key": agent_key,
            },
        )
        return [RetrievedChunk(**chunk) for chunk in data.get("chunks", [])]

    async def genie_query(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        connection: str,
        genie_space: str,
        question: str,
    ) -> dict[str, Any]:
        """Live Databricks Genie query through ACE (connection resolved from
        the team's connection registry — no Databricks creds agent-side)."""
        return await self._post(
            "/api/v1/capability/data/genie",
            {
                "envelope": envelope.to_payload(),
                "agent_key": agent_key,
                "connection": connection,
                "genie_space": genie_space,
                "question": question,
            },
        )

    async def llm_chat(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        deployment: str,
        messages: list[dict[str, str]],
    ) -> str:
        data = await self._post(
            "/api/v1/capability/llm/chat",
            {
                "envelope": envelope.to_payload(),
                "agent_key": agent_key,
                "deployment": deployment,
                "messages": messages,
            },
        )
        return str(data.get("text", ""))

    async def llm_chat_stream(
      self,
      *,
      envelope: ContextEnvelope,
      agent_key: str,
      deployment: str,
      messages: list[dict[str, str]],
    ):
        payload = {
            "envelope": envelope.to_payload(),
            "agent_key": agent_key,
            "deployment": deployment,
            "messages": messages,
        }
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds
        ) as client:
            async with client.stream(
                "POST", 
                "/api/v1/capability/llm/chat/stream", 
                json=payload,
                headers={**await self._headers(), "Accept": "text/event-stream"}
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    if event.get("done"):
                        return
                    token = str(event.get("text", ""))
                    if token:
                        yield token

    async def sms_send(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        to_number: str,
        body: str,
    ) -> str:
        data = await self._post(
            "/api/v1/capability/sms/send",
            {
                "envelope": envelope.to_payload(),
                "agent_key": agent_key,
                "to_number": to_number,
                "body": body,
            },
        )
        return str(data.get("message_sid", ""))

    async def accessible_agents(
        self, *, envelope: ContextEnvelope
    ) -> list[AccessibleAgent]:
        data = await self._post(
            "/api/v1/capability/agents/catalog",
            {"envelope": envelope.to_payload()},
        )
        return [AccessibleAgent(**agent) for agent in data.get("agents", [])]

    async def resolve_agent(
        self, *, envelope: ContextEnvelope, agent_key: str
    ) -> ResolvedAgent:
        """Runtime peer discovery for dynamic agent-to-agent A2A calls."""
        data = await self._post(
            "/api/v1/capability/agents/resolve",
            {"envelope": envelope.to_payload(), "agent_key": agent_key},
        )
        return ResolvedAgent(**data)
