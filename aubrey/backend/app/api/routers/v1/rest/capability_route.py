"""The service plane — how AGENTS call aubrey.

Agents own no database, no vector store, no model keys. They authenticate
with their team's service token (Bearer), declare which agent they are, and
forward the end user's identity in a context envelope. Aubrey verifies the
agent belongs to the token's team, re-enforces the user's roles with
Casbin, and only then serves:

    POST /capability/knowledge/retrieve   grant-scoped hybrid + 1-hop graph
    POST /capability/llm/chat/stream      SSE token stream (data: {"text"})

No CSRF here — this plane is bearer-token, not cookies.
"""

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .....config.application_context import get_application_context
from .....config.settings_validator import PlaceholderPolicy
from .....dto.base import ApiEnvelope
from .....dto.capability import (
    CatalogAgentModel,
    CatalogRequest,
    CatalogResponse,
    LlmStreamRequest,
    RetrievedChunkModel,
    RetrieveRequest,
    RetrieveResponse,
)
from .....entity.agents import TeamTokenEntity
from .....llm.azure_foundry import get_ace_azure_foundry
from .....security.service_auth import (
    enforce_agent_access,
    require_service_token,
    resolve_owned_agent,
)
from .....services.agents.agent_catalog_service import get_agent_catalog_service
from .....services.knowledge.retrieval_service import (
    RetrievalService,
    get_retrieval_service,
    get_retrieval_settings,
)
from .....utils.common.logger import Logger
from .....utils.errors import AppError, ValidationError

logger = Logger(__name__).get_logger()

capability_router = APIRouter(prefix="/capability", tags=["Capability"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _require_chat_configured() -> None:
    foundry_cfg = get_application_context().microsoft["azure"]["azure_foundry"]
    completion = foundry_cfg.get("text_completion") or {}
    checks = {
        "base_endpoint": foundry_cfg.get("base_endpoint"),
        "api_key": foundry_cfg.get("api_key"),
        "text_completion.deployment": completion.get("deployment"),
        "text_completion.api_version": completion.get("api_version"),
    }
    for key, value in checks.items():
        if not PlaceholderPolicy.is_configured(value):
            raise ValidationError(
                "The chat model is not configured. Set "
                f"microsoft.azure.azure_foundry.{key} in the env yaml."
            )


@capability_router.post(
    "/knowledge/retrieve", response_model=ApiEnvelope[RetrieveResponse]
)
async def retrieve_knowledge(
    body: RetrieveRequest,
    token: TeamTokenEntity = Depends(require_service_token),
    retrieval: RetrievalService = Depends(get_retrieval_service),
) -> ApiEnvelope[RetrieveResponse]:
    agent = await resolve_owned_agent(token=token, agent_key=body.agent_key)
    await enforce_agent_access(
        envelope=body.envelope, agent=agent, tenant_id=token.tenant_id
    )
    chunks = await retrieval.retrieve(
        tenant_id=token.tenant_id,
        agent_key=agent.agent_key,
        query=body.query,
        mode=body.mode,
        top_k=body.top_k,
        min_similarity=body.min_similarity,
    )
    return ApiEnvelope(
        data=RetrieveResponse(
            query=body.query,
            mode=(body.mode or get_retrieval_settings().mode),
            chunks=[
                RetrievedChunkModel(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    chunk_index=c.chunk_index,
                    file_name=c.file_name,
                    source_uri=c.source_uri,
                    content=c.content,
                    token_count=c.token_count,
                    score=c.score,
                    origin=c.origin,
                )
                for c in chunks
            ],
        )
    )


@capability_router.post(
    "/agents/catalog", response_model=ApiEnvelope[CatalogResponse]
)
async def agents_catalog(
    body: CatalogRequest,
    token: TeamTokenEntity = Depends(require_service_token),
) -> ApiEnvelope[CatalogResponse]:
    """Live Casbin-scoped catalog for the forwarded user — what the general
    agent grounds its 'what can I access?' answers on."""
    await resolve_owned_agent(token=token, agent_key=body.agent_key)
    agents = await get_agent_catalog_service().list_for(
        tenant_id=token.tenant_id, roles=tuple(body.envelope.roles)
    )
    return ApiEnvelope(
        data=CatalogResponse(
            agents=[
                CatalogAgentModel(
                    agent_key=a.agent_key,
                    display_name=a.display_name,
                    description=a.description,
                    team_key=a.team_key,
                )
                for a in agents
            ]
        )
    )


@capability_router.post("/llm/chat/stream")
async def llm_chat_stream(
    body: LlmStreamRequest,
    token: TeamTokenEntity = Depends(require_service_token),
) -> StreamingResponse:
    agent = await resolve_owned_agent(token=token, agent_key=body.agent_key)
    await enforce_agent_access(
        envelope=body.envelope, agent=agent, tenant_id=token.tenant_id
    )
    _require_chat_configured()
    if not body.messages:
        raise ValidationError("llm/chat/stream needs at least one message.")
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    async def event_stream():
        try:
            async for text in get_ace_azure_foundry().astream_chat(
                messages=messages, max_output_tokens=body.max_output_tokens
            ):
                yield f"data: {json.dumps({'text': text})}\n\n"
            yield 'data: {"done": true}\n\n'
        except Exception as exc:  # noqa: BLE001 — stream already started; surface, never mask
            code = exc.code if isinstance(exc, AppError) else "llm_stream_failed"
            message = (
                exc.client_message()
                if isinstance(exc, AppError)
                else "The chat model call failed mid-stream."
            )
            logger.error(
                "LLM stream failed",
                extra={"agent_key": agent.agent_key, "code": code},
                exc_info=True,
            )
            yield f"data: {json.dumps({'error': {'code': code, 'message': message}})}\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
