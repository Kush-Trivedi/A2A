"""The kit's tiny A2A sender for delegation (M5) — after aubrey's
/capability/agents/resolve authorizes a hop, the agent talks to the peer's
card_url DIRECTLY (agent-to-agent, streaming), forwarding the fresh
platform-signed envelope verbatim. Mirrors the platform's client-service
pattern (card resolve -> create_client -> send_message) at the minimum an
agent needs: text chunks out, everything else ignored."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest

_WELL_KNOWN_MARKER = "/.well-known/"


def split_card_url(card_url: str) -> tuple[str, str]:
    """(base_url, relative_card_path) from a full card URL."""
    base, marker, rest = card_url.partition(_WELL_KNOWN_MARKER)
    if marker:
        return base, f"{marker}{rest}"
    return card_url.rstrip("/"), "/.well-known/agent-card.json"


async def stream_peer_text(
    *,
    card_url: str,
    text: str,
    context_id: str = "",
    metadata: dict[str, Any] | None = None,
    reference_task_ids: tuple[str, ...] = (),
    timeout_seconds: float = 120.0,
) -> AsyncIterator[str]:
    """Send one message to a peer A2A agent and yield its text chunks.

    `context_id` should be the chat session id (the platform's session =
    contextId convention) so the whole consultation stays one thread;
    `metadata` carries the signed envelope under aubrey.context/v1;
    `reference_task_ids` links the peer's task to the caller's current
    task for lineage. Errors propagate to the caller — no masking."""
    message = new_text_message(
        text, context_id=context_id or uuid.uuid4().hex, role=Role.ROLE_USER
    )
    if metadata:
        message.metadata.update(metadata)
    for task_id in reference_task_ids:
        if task_id:
            message.reference_task_ids.append(task_id)
    request = SendMessageRequest(message=message)

    base_url, relative_card_path = split_card_url(card_url)
    httpx_client = httpx.AsyncClient(timeout=timeout_seconds)
    client = None
    try:
        resolver = A2ACardResolver(
            httpx_client, base_url, agent_card_path=relative_card_path
        )
        card = await resolver.get_agent_card()
        client = await create_client(
            card, ClientConfig(streaming=True, httpx_client=httpx_client)
        )
        async for response in client.send_message(request):
            payload = response.WhichOneof("payload")
            if payload == "message":
                chunk = get_message_text(response.message) or ""
                if chunk:
                    yield chunk
            elif payload == "status_update":
                update = response.status_update
                if update.status.HasField("message"):
                    chunk = get_message_text(update.status.message) or ""
                    if chunk:
                        yield chunk
            elif payload == "task":
                task = response.task
                if task.status.HasField("message"):
                    chunk = get_message_text(task.status.message) or ""
                    if chunk:
                        yield chunk
    finally:
        if client is not None:
            await client.close()
        await httpx_client.aclose()
