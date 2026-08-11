import json
from .....dto.common import ApiEnvelope
from collections.abc import AsyncIterator
from .....utils.common.logger import Logger
from fastapi import APIRouter, Depends, status
from .....services.agents.agent_catalog_service import AgentCatalogService
from fastapi.responses import StreamingResponse
from .....security.session import SessionContext
from .....security.dependencies import require_csrf
from .....security.rate_limiter import get_rate_limiter
from .....entity.chat.chat_message_entity import ChatMessageEntity
from .....entity.chat.chat_session_entity import ChatSessionEntity
from .....security.authorization.dependencies import require_permission
from ....dependencies import provide_agent_catalog_service, provide_conversation_service

from .....dto.chat import (
    AgentSummaryModel,
    ChatMessageModel,
    ChatSourceModel,
    ChatTurnResponse,
    CreateSessionRequest,
    EditMessageRequest,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    RenameSessionRequest,
    SendChatRequest,
    SessionMessagesResponse,
    SessionSummaryModel,
)

from .....services.conversation.conversation_service import (
    ChatTurnResult,
    ConversationService,
)




logger = Logger(__name__).get_logger()

chat_v1_router = APIRouter(prefix="/chat", tags=["Chat"])

def _to_turn_response(result: ChatTurnResult) -> ChatTurnResponse:
    return ChatTurnResponse(
        session_id=result.session_id,
        message_id=result.message_id,
        agent_id=result.agent_id,
        answer=result.answer,
        sources=[
            ChatSourceModel(
                document_id=s.document_id,
                source_name=s.source_name,
                knowledge_source=s.knowledge_source,
                score=s.score,
            )
            for s in result.sources
        ],
        refusal=result.refusal,
        disambiguation=result.disambiguation,
    )


def _to_session_summary(entity: ChatSessionEntity) -> SessionSummaryModel:
    return SessionSummaryModel(
        id=entity.id,
        title=entity.title,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def _to_message_model(entity: ChatMessageEntity) -> ChatMessageModel:
    try:
        metadata = json.loads(entity.metadata_json or "{}")
    except (ValueError, TypeError):
        metadata = {}
    return ChatMessageModel(
        id=entity.id,
        role=entity.role,
        content=entity.content,
        created_at=entity.created_at,
        metadata=metadata if isinstance(metadata, dict) else {},
        feedback=entity.feedback,
        edited=bool(entity.edit_chain_id),
    )


def _sse(event: dict) -> str:
    name = event.get("event", "message")
    payload = json.dumps(event.get("data", {}), default=str)
    return f"event: {name}\ndata: {payload}\n\n"


@chat_v1_router.post("", response_model=ApiEnvelope[ChatTurnResponse])
async def send_chat(
    payload: SendChatRequest,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[ChatTurnResponse]:
    await get_rate_limiter().check(context.actor_id)
    result = await service.send(
        context=context,
        agent_id=payload.agent,
        message=payload.message,
        session_id=payload.session_id,
    )
    return ApiEnvelope(data=_to_turn_response(result))


@chat_v1_router.post("/stream")
async def stream_chat(
    payload: SendChatRequest,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> StreamingResponse:
    await get_rate_limiter().check(context.actor_id)

    async def _event_stream() -> AsyncIterator[str]:
        async for event in service.stream(
            context=context,
            agent_id=payload.agent,
            message=payload.message,
            session_id=payload.session_id,
        ):
            yield _sse(event)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_v1_router.get("/agents", response_model=ApiEnvelope[list[AgentSummaryModel]])
async def list_agents(
    context: SessionContext = Depends(require_permission("chat", "read")),
    catalog: AgentCatalogService = Depends(provide_agent_catalog_service),
) -> ApiEnvelope[list[AgentSummaryModel]]:
    agents = [
        AgentSummaryModel(id=a.id, display_name=a.display_name, description=a.description)
        for a in await catalog.list_for(context)
    ]
    return ApiEnvelope(data=agents)


@chat_v1_router.get("/sessions", response_model=ApiEnvelope[list[SessionSummaryModel]])
async def list_sessions(
    context: SessionContext = Depends(require_permission("chat", "read")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[list[SessionSummaryModel]]:
    sessions = await service.list_sessions(context=context)
    return ApiEnvelope(data=[_to_session_summary(s) for s in sessions])


@chat_v1_router.post(
    "/sessions",
    response_model=ApiEnvelope[SessionSummaryModel],
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: CreateSessionRequest,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[SessionSummaryModel]:
    session = await service.create_session(context=context, title=payload.title)
    return ApiEnvelope(data=_to_session_summary(session), message="Session created successfully.")



@chat_v1_router.patch(
    "/sessions/{session_id}", response_model=ApiEnvelope[SessionSummaryModel]
)
async def rename_session(
    session_id: str,
    payload: RenameSessionRequest,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[SessionSummaryModel]:
    session = await service.rename_session(
        context=context, session_id=session_id, title=payload.title
    )
    return ApiEnvelope(data=_to_session_summary(session))


@chat_v1_router.delete("/sessions/{session_id}", response_model=ApiEnvelope[dict])
async def delete_session(
    session_id: str,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[dict]:
    """Soft-delete (archive) a session and its owner-scoped uploads."""
    await service.archive_session(context=context, session_id=session_id)
    return ApiEnvelope(data={"archived": True}, message="Session archived.")


@chat_v1_router.get(
    "/sessions/{session_id}/messages",
    response_model=ApiEnvelope[SessionMessagesResponse],
)
async def get_session_messages(
    session_id: str,
    context: SessionContext = Depends(require_permission("chat", "read")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[SessionMessagesResponse]:
    messages = await service.get_messages(context=context, session_id=session_id)
    return ApiEnvelope(
        data=SessionMessagesResponse(
            session_id=session_id,
            messages=[_to_message_model(m) for m in messages],
        )
    )


@chat_v1_router.post(
    "/sessions/{session_id}/messages/{message_id}/edit",
    response_model=ApiEnvelope[ChatTurnResponse],
)
async def edit_message(
    session_id: str,
    message_id: str,
    payload: EditMessageRequest,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[ChatTurnResponse]:
    result = await service.edit_message(
        context=context,
        session_id=session_id,
        message_id=message_id,
        content=payload.content,
    )
    return ApiEnvelope(data=_to_turn_response(result))

@chat_v1_router.post(
    "/sessions/{session_id}/messages/{message_id}/feedback",
    response_model=ApiEnvelope[MessageFeedbackResponse],
)
async def set_message_feedback(
    session_id: str,
    message_id: str,
    payload: MessageFeedbackRequest,
    context: SessionContext = Depends(require_csrf),
    _perm: SessionContext = Depends(require_permission("chat", "write")),
    service: ConversationService = Depends(provide_conversation_service),
) -> ApiEnvelope[MessageFeedbackResponse]:
    message = await service.set_message_feedback(
        context=context,
        session_id=session_id,
        message_id=message_id,
        feedback=payload.feedback,
    )
    return ApiEnvelope(data=MessageFeedbackResponse(message_id=message.id, feedback=message.feedback))




