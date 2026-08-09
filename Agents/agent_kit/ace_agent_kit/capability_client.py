from dataclasses import dataclass
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


class AceCapabilityClient:
    """Team agents' gateway to ACE capabilities (retrieve, catalog).

    Always sends the ContextEnvelope so ACE enforces the CALLER's roles per
    knowledge source. Sends a service bearer when a token provider and
    audience are configured (required once ACE capability auth is enabled).
    """

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
            },
        )
        return [RetrievedChunk(**chunk) for chunk in data.get("chunks", [])]

    async def llm_chat(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        deployment: str,
        messages: list[dict[str, str]],
    ) -> str:
        """Chat completion via ACE using one of THIS team's registered
        deployments — ACE validates the deployment and holds the API key."""
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

    async def sms_send(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        to_number: str,
        body: str,
    ) -> str:
        """Outreach SMS via ACE — Twilio credentials never leave ACE;
        opt-outs are enforced centrally."""
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
