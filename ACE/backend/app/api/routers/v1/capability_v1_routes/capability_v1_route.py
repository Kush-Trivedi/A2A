from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import json

from ace_agent_kit import ContextEnvelope

from .....dto.capability import (
    CapabilityAgentModel,
    CapabilityCatalogRequest,
    CapabilityCatalogResponse,
    CapabilityChunkModel,
    CapabilityEnvelopeModel,
    CapabilityLlmChatRequest,
    CapabilityLlmChatResponse,
    CapabilityRetrieveRequest,
    CapabilityRetrieveResponse,
    CapabilitySmsSendRequest,
    CapabilitySmsSendResponse,
)
from .....services.sms import get_sms_channel_service
from .....services.a2a.llm_gateway_service import (
    LlmGatewayService,
    get_llm_gateway_service,
)
from .....dto.common import ApiEnvelope
from .....security.service_auth import get_service_auth_guard
from .....security.session import SessionContext
from .....services.agents.agent_catalog_service import AgentCatalogService
from .....services.embedding.embedding_service import EmbeddingService
from .....services.knowledge.gateway import KnowledgeGateway, get_knowledge_gateway
from ....dependencies import (
    provide_agent_catalog_service,
    provide_embedding_service,
)

capability_v1_router = APIRouter(prefix="/capability", tags=["Capability (service-plane)"])


async def require_service_auth(request: Request) -> None:
    await get_service_auth_guard().authenticate(request)


class EnvelopeContextMapper:
    @staticmethod
    def to_context(envelope: CapabilityEnvelopeModel) -> SessionContext:
        now = datetime.now(timezone.utc)
        return SessionContext(
            session_id=envelope.chat_session_id or "service-plane",
            tenant_id=envelope.tenant_id,
            user_id=envelope.user_id,
            actor_id=envelope.actor_id,
            email="",
            display_name="",
            auth_provider="service",
            csrf_token="",
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(minutes=5),
            roles=tuple(envelope.roles),
        )


@capability_v1_router.post(
    "/knowledge/retrieve",
    response_model=ApiEnvelope[CapabilityRetrieveResponse],
    dependencies=[Depends(require_service_auth)],
)
async def retrieve_knowledge(
    body: CapabilityRetrieveRequest,
    embedding: EmbeddingService = Depends(provide_embedding_service),
) -> ApiEnvelope[CapabilityRetrieveResponse]:
    gateway: KnowledgeGateway = get_knowledge_gateway()
    context = EnvelopeContextMapper.to_context(body.envelope)
    vector = (
        [] if body.retrieval_mode == "sparse" else await embedding.embed_query(body.query)
    )
    chunks = await gateway.retrieve(
        context=context,
        embedding=vector,
        requested_sources=tuple(body.knowledge_sources),
        session_id=body.session_id,
        top_k=body.top_k,
        query_text=body.query,
        mode=body.retrieval_mode or "",
    )
    return ApiEnvelope(
        data=CapabilityRetrieveResponse(
            chunks=[
                CapabilityChunkModel(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    source_name=c.source_name,
                    knowledge_source=c.knowledge_source,
                    content=c.content,
                    score=c.score,
                    metadata=c.metadata,
                )
                for c in chunks
            ]
        )
    )


@capability_v1_router.post(
    "/llm/chat",
    response_model=ApiEnvelope[CapabilityLlmChatResponse],
    dependencies=[Depends(require_service_auth)],
)
async def llm_chat(
    body: CapabilityLlmChatRequest,
) -> ApiEnvelope[CapabilityLlmChatResponse]:
    service: LlmGatewayService = get_llm_gateway_service()
    envelope = ContextEnvelope(
        tenant_id=body.envelope.tenant_id,
        actor_id=body.envelope.actor_id,
        user_id=body.envelope.user_id,
        roles=tuple(body.envelope.roles),
        correlation_id=body.envelope.correlation_id,
        chat_session_id=body.envelope.chat_session_id,
    )
    text = await service.chat(
        envelope=envelope,
        agent_key=body.agent_key,
        deployment=body.deployment,
        messages=[m.model_dump() for m in body.messages],
    )
    return ApiEnvelope(
        data=CapabilityLlmChatResponse(text=text, deployment=body.deployment)
    )


@capability_v1_router.post(
    "/llm/chat/stream",
    dependencies=[Depends(require_service_auth)],
)
async def stream_llm_chat(body: CapabilityLlmChatRequest) -> StreamingResponse:
    service: LlmGatewayService = get_llm_gateway_service()
    envelope = ContextEnvelope(
        tenant_id=body.envelope.tenant_id,
        actor_id=body.envelope.actor_id,
        user_id=body.envelope.user_id,
        roles=tuple(body.envelope.roles),
        correlation_id=body.envelope.correlation_id,
        chat_session_id=body.envelope.chat_session_id,
    )

    async def events():
        async for token in service.stream_chat(
            envelope=envelope,
            agent_key=body.agent_key,
            deployment=body.deployment,
            messages=[m.model_dump() for m in body.messages],
        ):
            yield f"data: {json.dumps({'text': token})}\n\n"
        yield "data: {\"done\": true}\n\n"

    return StreamingResponse(
        events(), \
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@capability_v1_router.post(
    "/sms/send",
    response_model=ApiEnvelope[CapabilitySmsSendResponse],
    dependencies=[Depends(require_service_auth)],
)
async def sms_send(
    body: CapabilitySmsSendRequest,
) -> ApiEnvelope[CapabilitySmsSendResponse]:
    sid = await get_sms_channel_service().send_outreach(
        tenant_id=body.envelope.tenant_id,
        to_number=body.to_number,
        body=body.body,
        agent_key=body.agent_key,
    )
    return ApiEnvelope(data=CapabilitySmsSendResponse(message_sid=sid))


@capability_v1_router.post(
    "/agents/catalog",
    response_model=ApiEnvelope[CapabilityCatalogResponse],
    dependencies=[Depends(require_service_auth)],
)
async def agent_catalog(
    body: CapabilityCatalogRequest,
    catalog: AgentCatalogService = Depends(provide_agent_catalog_service),
) -> ApiEnvelope[CapabilityCatalogResponse]:
    context = EnvelopeContextMapper.to_context(body.envelope)
    agents = await catalog.list_for(context)
    return ApiEnvelope(
        data=CapabilityCatalogResponse(
            agents=[
                CapabilityAgentModel(
                    id=a.id,
                    display_name=a.display_name,
                    description=a.description,
                    team_key=a.team_key,
                    is_remote=a.is_remote,
                )
                for a in agents
            ]
        )
    )
