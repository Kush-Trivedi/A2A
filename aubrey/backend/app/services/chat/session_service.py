"""Chat sessions + messages — the durable conversation record.

Every surface (web now, SMS later) shares this model: a session is owned by
(tenant, user), its id doubles as the A2A contextId, and every turn's
messages land here — data at rest, always. Stickiness reads the last
ANSWER message's agent_key from metadata; routing messages never stick."""

import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.chat import (
    ChatMessageEntity, ChatSessionEntity, 
    MessageEditChainEntity, MessageEditVersionEntity, MessageFeedbackEntity,
    MessageKind, MessageRole
)
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError, ValidationError

logger = Logger(__name__).get_logger()

_TITLE_MAX_CHARS = 36
_FEEDBACK_VALUES = {"angry", "sad", "neutral", "happy", "very_happy"}
_FEEDBACK_MAX_CHARS = 2_000


def _title_from(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= _TITLE_MAX_CHARS:
        return cleaned
    cut = cleaned[:_TITLE_MAX_CHARS]
    if " " in cut[20:]:
        cut = cut[: cut.rindex(" ")]
    return f"{cut}…"


class ChatSessionService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def create_session(
        self, *, context: SessionContext, title: str = "", channel: str = "web"
    ) -> ChatSessionEntity:
        session_id = uuid.uuid4().hex
        try:
            async with self._db.session() as session:
                entity = ChatSessionEntity(
                    id=session_id,
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    actor_id=context.actor_id,
                    title=title.strip(),
                    channel=channel,
                )
                session.add(entity)
                return entity
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list_sessions(self, *, context: SessionContext) -> list[ChatSessionEntity]:
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(ChatSessionEntity)
                        .where(
                            ChatSessionEntity.tenant_id == context.tenant_id,
                            ChatSessionEntity.user_id == context.user_id,
                            ChatSessionEntity.archived_at.is_(None),  # type: ignore[union-attr]
                        )
                        .order_by(ChatSessionEntity.updated_at.desc())  # type: ignore[attr-defined]
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def get_owned_session(
        self, *, context: SessionContext, session_id: str
    ) -> ChatSessionEntity:
        try:
            async with self._db.session() as session:
                entity = (
                    await session.exec(
                        select(ChatSessionEntity).where(
                            ChatSessionEntity.id == session_id,
                            ChatSessionEntity.tenant_id == context.tenant_id,
                            ChatSessionEntity.user_id == context.user_id,
                            ChatSessionEntity.archived_at.is_(None),  # type: ignore[union-attr]
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if entity is None:
            raise NotFoundError(
                "Chat session not found.", details={"session_id": session_id}
            )
        return entity

    async def archive_session(
        self, *, context: SessionContext, session_id: str
    ) -> None:
        owned = await self.get_owned_session(context=context, session_id=session_id)
        try:
            async with self._db.session() as session:
                row = (
                    await session.exec(
                        select(ChatSessionEntity).where(ChatSessionEntity.id == owned.id)
                    )
                ).one()
                row.archived_at = datetime.now(timezone.utc)
                row.updated_at = datetime.now(timezone.utc)
                session.add(row)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def append_message(
        self,
        *,
        context: SessionContext,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessageEntity:
        if role not in (MessageRole.USER, MessageRole.ASSISTANT):
            raise ValidationError(f"Unknown message role '{role}'.")
        try:
            async with self._db.session() as session:
                thread = (
                    await session.exec(
                        select(ChatSessionEntity).where(
                            ChatSessionEntity.id == session_id,
                            ChatSessionEntity.tenant_id == context.tenant_id,
                        )
                    )
                ).first()
                if thread is None:
                    raise NotFoundError(
                        "Chat session not found.", details={"session_id": session_id}
                    )
                message = ChatMessageEntity(
                    id=uuid.uuid4().hex,
                    session_id=session_id,
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    actor_id=context.actor_id,
                    role=role,
                    content=content,
                    message_metadata=dict(metadata or {}),
                )
                session.add(message)
                if role == MessageRole.USER and not thread.title:
                    thread.title = _title_from(content)
                thread.updated_at = datetime.now(timezone.utc)
                session.add(thread)
                return message
        except (NotFoundError, ValidationError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list_messages(
        self, *, context: SessionContext, session_id: str
    ) -> list[ChatMessageEntity]:
        await self.get_owned_session(context=context, session_id=session_id)
        return await self._messages(context.tenant_id, session_id)

    async def feedback_by_message(
        self, *, context: SessionContext, sesssion_id: str
    ) -> dict[str, str]:
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(MessageFeedbackEntity)
                        .where(
                            MessageFeedbackEntity.tenant_id == context.tenant_id,
                            MessageFeedbackEntity.session_id == sesssion_id,
                            MessageFeedbackEntity.actor_id == context.actor_id,
                            MessageFeedbackEntity.deleted_at.is_(None)
                        )
                    )
                ).all()
                return {row.message_id: row.value for row in rows}
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


    async def edited_message_ids(
        self, *, context: SessionContext, session_id: str
    ) -> set[str]:
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(MessageEditChainEntity.id)
                        .where(
                            MessageEditChainEntity.session_id == session_id,
                            MessageEditChainEntity.tenant_id == context.tenant_id,
                        )
                    )
                ).all()
                return {row for row in rows}
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def set_message_feedback(
        self, *, context: SessionContext, session_id: str, message_id: str, feedback: str
    ) -> str |None:
        message = await self._owned_message(
            context=context,
            session_id=session_id,
            message_id=message_id,
        )
        if message.role != MessageRole.ASSISTANT:
            raise ValidationError("Only assistant messages can have feedback.")

        value = (feedback or "").strip()
        if value and value not in _FEEDBACK_VALUES and len(value) > _FEEDBACK_MAX_CHARS:
            raise ValidationError(f"Feedback must be one of {_FEEDBACK_VALUES} or at most {_FEEDBACK_MAX_CHARS} characters.")
        try:
            async with self._db.session() as session:
                record = (
                    await session.exec(
                        select(MessageFeedbackEntity).where(
                            MessageFeedbackEntity.message_id == message_id,
                            MessageFeedbackEntity.actor_id == context.actor_id,
                        )
                    )
                ).first()
                if not value:
                    if record is not None:
                        record.deleted_at = datetime.now(timezone.utc)
                        session.add(record)
                    return None
                if record is None:
                    record = MessageFeedbackEntity(
                        id=uuid.uuid4().hex,
                        message_id=message_id,
                        session_id=session_id,
                        tenant_id=context.tenant_id,
                        actor_id=context.actor_id,
                        feedback=value,
                    )
                else:
                    record.feedback = value
                    record.deleted_at = None
                session.add(record)
                return value
        except ValidationError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


    async def edit_user_message(
        self, *, context: SessionContext, session_id: str, message_id: str, content: str
    ) -> ChatMessageEntity:
        value = content.strip()
        if not value:
            raise ValidationError("Content cannot be empty.")
        message = await self._owned_message(
            context=context,
            session_id=session_id,
            message_id=message_id,
        )
        if message.role != MessageRole.USER:
            raise ValidationError("Only user messages can be edited.")

        if message.content == value:
            return message

        try:
            async with self._db.session() as session:
                editable = await session.get(ChatMessageEntity, message.id)
                if editable is None:
                    raise ValidationError("Message not found.")
                chain = (
                    await session.exec(
                        select(MessageEditChainEntity).where(
                            MessageEditChainEntity.message_id == message.id
                        )
                    )
                ).first()
                if chain is None:
                    chain = MessageEditChainEntity(
                        id=uuid.uuid4().hex,
                        message_id=message.id,
                        session_id=session_id,
                        tenant_id=context.tenant_id,
                        actor_id=context.actor_id,
                    )
                    session.add(chain)
                    version_number = 1
                else:
                    versions = (
                        await session.exec(
                            select(MessageEditVersionEntity).where(
                                MessageEditVersionEntity.chain_id == chain.id
                            )
                        )
                    ).all()
                    version_number = len(versions) + 1
                session.add(
                    MessageEditVersionEntity(
                        id=uuid.uuid4().hex,
                        chain_id=chain.id,
                        version_number=version_number,
                        content=editable.content,
                    )
                )
                editable.content = value
                editable.updated_at = datetime.now(tz=timezone.utc)
                session.add(editable)
                return editable
        except ValidationError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _messages(
        self, tenant_id: str, session_id: str
    ) -> list[ChatMessageEntity]:
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(ChatMessageEntity)
                        .where(
                            ChatMessageEntity.session_id == session_id,
                            ChatMessageEntity.tenant_id == tenant_id,
                        )
                        .order_by(ChatMessageEntity.created_at)  # type: ignore[arg-type]
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


    async def _owned_message(
        self, *, context:SessionContext, session_id: str, message_id: str
    ) -> ChatMessageEntity:
        await self.get_owned_session(content=context, session_id=session_id)

        try:
            async with self._db.session() as session:
                message = (
                    await session.exec(
                        select(ChatMessageEntity).where(
                            ChatMessageEntity.id == message_id,
                            ChatMessageEntity.session_id == session_id,
                            ChatMessageEntity.tenant_id == context.tenant_id,
                            ChatMessageEntity.user_id == context.user_id,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if message is None:
            raise NotFoundError(f"Message with ID {message_id} not found")
        return message

    async def sticky_agent(self, *, tenant_id: str, session_id: str) -> str | None:
        """The agent that answered last — follow-ups prefer it. Only ANSWER
        messages count; routing messages carry no stickiness."""
        messages = await self._messages(tenant_id, session_id)
        for message in reversed(messages):
            if message.role != MessageRole.ASSISTANT:
                continue
            meta = message.message_metadata or {}
            if meta.get("kind") == MessageKind.ANSWER and meta.get("agent_key"):
                return str(meta["agent_key"])
        return None


_service: ChatSessionService | None = None


def get_chat_session_service() -> ChatSessionService:
    global _service
    if _service is None:
        _service = ChatSessionService()
    return _service
