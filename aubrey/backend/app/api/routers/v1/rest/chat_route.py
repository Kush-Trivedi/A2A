"""Chat sessions — the user-plane conversation surface. The streaming turn
endpoint (POST /chat/stream) joins this router at M3; sessions and their
history are the durable substrate it writes to."""

from fastapi import APIRouter, Depends, status

from .....dto.base import ApiEnvelope, MessageResponse
from .....dto.chat import ChatMessageModel, ChatSessionModel, CreateSessionRequest
from .....entity.chat import ChatMessageEntity, ChatSessionEntity
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.chat import ChatSessionService
from ....dependencies import provide_chat_session_service

chat_router = APIRouter(prefix="/chat", tags=["Chat"])

_CHAT_OBJ = "/api/v1/chat"


def _to_session(entity: ChatSessionEntity) -> ChatSessionModel:
    return ChatSessionModel(
        id=entity.id,
        title=entity.title,
        channel=entity.channel,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def _to_message(entity: ChatMessageEntity) -> ChatMessageModel:
    return ChatMessageModel(
        id=entity.id,
        session_id=entity.session_id,
        role=entity.role,
        content=entity.content,
        metadata=dict(entity.message_metadata or {}),
        created_at=entity.created_at,
    )


@chat_router.post(
    "/sessions",
    response_model=ApiEnvelope[ChatSessionModel],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_CHAT_OBJ, "POST"))],
)
async def create_session(
    body: CreateSessionRequest,
    context: SessionContext = Depends(get_current_context),
    service: ChatSessionService = Depends(provide_chat_session_service),
) -> ApiEnvelope[ChatSessionModel]:
    session = await service.create_session(context=context, title=body.title)
    return ApiEnvelope(data=_to_session(session), message="Session created.")


@chat_router.get(
    "/sessions",
    response_model=ApiEnvelope[list[ChatSessionModel]],
    dependencies=[Depends(require_permission(_CHAT_OBJ, "GET"))],
)
async def list_sessions(
    context: SessionContext = Depends(get_current_context),
    service: ChatSessionService = Depends(provide_chat_session_service),
) -> ApiEnvelope[list[ChatSessionModel]]:
    sessions = await service.list_sessions(context=context)
    return ApiEnvelope(data=[_to_session(s) for s in sessions])


@chat_router.get(
    "/sessions/{session_id}/messages",
    response_model=ApiEnvelope[list[ChatMessageModel]],
    dependencies=[Depends(require_permission(_CHAT_OBJ, "GET"))],
)
async def list_messages(
    session_id: str,
    context: SessionContext = Depends(get_current_context),
    service: ChatSessionService = Depends(provide_chat_session_service),
) -> ApiEnvelope[list[ChatMessageModel]]:
    messages = await service.list_messages(context=context, session_id=session_id)
    return ApiEnvelope(data=[_to_message(m) for m in messages])


@chat_router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission(_CHAT_OBJ, "DELETE"))],
)
async def archive_session(
    session_id: str,
    context: SessionContext = Depends(get_current_context),
    service: ChatSessionService = Depends(provide_chat_session_service),
) -> MessageResponse:
    await service.archive_session(context=context, session_id=session_id)
    return MessageResponse(detail="Session archived.")
