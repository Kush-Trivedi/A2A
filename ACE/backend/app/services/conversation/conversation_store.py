import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlmodel import select
from ...entity.chat.chat_message_entity import ChatMessageEntity
from ...entity.chat.chat_session_entity import ChatSessionEntity
from ...entity.chat.message_edit_chain_entity import MessageEditChainEntity
from ...entity.chat.message_edit_version_entity import MessageEditVersionEntity
from ...entity.chat.message_feedback_entity import MessageFeedbackEntity
from ...security.session import SessionContext
from ...database.rdbms.pg_session import get_postgres_connector
from ...utils.common.logger import Logger
from ...utils.errors import BadRequestError, DatabaseError, NotFoundError

logger = Logger(__name__).get_logger()

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
DEFAULT_SESSION_TITLE = "New chat"
MAX_SESSION_TITLE_LENGTH = 36


@dataclass(frozen=True)
class EditedMessage:
    new_message: ChatMessageEntity
    previous_agent_id: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _title_from_message(content: str) -> str:
    normalized = " ".join((content or "").split())
    if not normalized:
        return DEFAULT_SESSION_TITLE
    if len(normalized) <= MAX_SESSION_TITLE_LENGTH:
        return normalized

    candidate = normalized[: MAX_SESSION_TITLE_LENGTH + 1]
    word_boundary = candidate.rfind(" ")
    cutoff = word_boundary if word_boundary >= 20 else MAX_SESSION_TITLE_LENGTH
    return f"{normalized[:cutoff].rstrip()}…"


class ConversationStore:
    def __init__(self) -> None:
        self._connector = get_postgres_connector()

    async def create_session(
        self, *, context: SessionContext, title: str
    ) -> ChatSessionEntity:
        now = _now()
        entity = ChatSessionEntity(
            id=_new_id("session"),
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            title=(title or "").strip()[:120] or "New chat",
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._connector.session() as session:
                session.add(entity)
        except Exception as exc:  # noqa: BLE001
            logger.error("create_session failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc
        return entity

    async def list_sessions(self, *, context: SessionContext) -> list[ChatSessionEntity]:
        stmt = (
            select(ChatSessionEntity)
            .where(
                ChatSessionEntity.tenant_id == context.tenant_id,
                ChatSessionEntity.user_id == context.user_id,
                ChatSessionEntity.archived_at.is_(None),
            )
            .order_by(ChatSessionEntity.updated_at.desc())
        )
        async with self._connector.session() as session:
            sessions = list((await session.exec(stmt)).all())
            for chat_session in sessions:
                if chat_session.title.casefold() != DEFAULT_SESSION_TITLE.casefold():
                    continue
                first_message_stmt = (
                    select(ChatMessageEntity.content)
                    .where(
                        ChatMessageEntity.tenant_id == context.tenant_id,
                        ChatMessageEntity.session_id == chat_session.id,
                        ChatMessageEntity.user_id == context.user_id,
                        ChatMessageEntity.role == ROLE_USER,
                    )
                    .order_by(ChatMessageEntity.created_at.asc())
                    .limit(1)
                )
                first_content = (await session.exec(first_message_stmt)).first()
                if first_content:
                    chat_session.title = _title_from_message(first_content)
                    chat_session.updated_at = _now()
                    session.add(chat_session)
            return sessions

    async def get_owned_session(
        self, *, context: SessionContext, session_id: str
    ) -> ChatSessionEntity:
        entity = await self._load_owned_session(context=context, session_id=session_id)
        if entity is None:
            raise NotFoundError("Chat session not found.", details={"session_id": session_id})
        return entity

    async def rename_session(
        self, *, context: SessionContext, session_id: str, title: str
    ) -> ChatSessionEntity:
        async with self._connector.session() as session:
            entity = await self._fetch_owned(session, context, session_id)
            if entity is None:
                raise NotFoundError("Chat session not found.", details={"session_id": session_id})
            entity.title = (title or "").strip()[:120] or entity.title
            entity.updated_at = _now()
            session.add(entity)
            return entity

    async def archive_session(
        self, *, context: SessionContext, session_id: str
    ) -> None:
        async with self._connector.session() as session:
            entity = await self._fetch_owned(session, context, session_id)
            if entity is None:
                raise NotFoundError("Chat session not found.", details={"session_id": session_id})
            if entity.archived_at is None:
                entity.archived_at = _now()
                session.add(entity)


    async def add_message(
        self,
        *,
        context: SessionContext,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessageEntity:
        now = _now()
        message = ChatMessageEntity(
            id=_new_id("msg"),
            session_id=session_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            role=role,
            content=content,
            metadata_json=json.dumps(metadata or {}, default=str),
            created_at=now,
        )
        try:
            async with self._connector.session() as session:
                owned = await self._fetch_owned(session, context, session_id)
                if owned is None:
                    raise NotFoundError(
                        "Chat session not found.", details={"session_id": session_id}
                    )
                session.add(message)
                if (
                    role == ROLE_USER
                    and owned.title.casefold() == DEFAULT_SESSION_TITLE.casefold()
                ):
                    owned.title = _title_from_message(content)
                owned.updated_at = now
                session.add(owned)
        except NotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("add_message failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc
        return message

    async def list_messages(
        self, *, context: SessionContext, session_id: str
    ) -> list[ChatMessageEntity]:
        await self.get_owned_session(context=context, session_id=session_id)
        stmt = (
            select(ChatMessageEntity)
            .where(
                ChatMessageEntity.tenant_id == context.tenant_id,
                ChatMessageEntity.session_id == session_id,
                ChatMessageEntity.is_superseded.is_(False),
            )
            .order_by(ChatMessageEntity.created_at.asc())
        )
        async with self._connector.session() as session:
            return list((await session.exec(stmt)).all())

    async def edit_user_message(
        self,
        *,
        context: SessionContext,
        session_id: str,
        message_id: str,
        content: str,
    ) -> EditedMessage:
        now = _now()
        async with self._connector.session() as db:
            owned_stmt = (
                select(ChatSessionEntity)
                .where(
                    ChatSessionEntity.id == session_id,
                    ChatSessionEntity.tenant_id == context.tenant_id,
                    ChatSessionEntity.user_id == context.user_id,
                    ChatSessionEntity.archived_at.is_(None),
                )
                .with_for_update()
            )
            owned = (await db.exec(owned_stmt)).first()
            if owned is None:
                raise NotFoundError(
                    "Chat session not found.", details={"session_id": session_id}
                )

            active_stmt = (
                select(ChatMessageEntity)
                .where(
                    ChatMessageEntity.tenant_id == context.tenant_id,
                    ChatMessageEntity.session_id == session_id,
                    ChatMessageEntity.is_superseded.is_(False),
                )
                .order_by(ChatMessageEntity.created_at.asc())
            )
            active = list((await db.exec(active_stmt)).all())

            index = next((i for i, m in enumerate(active) if m.id == message_id), None)
            if index is None:
                raise NotFoundError(
                    "Message not found.", details={"message_id": message_id}
                )

            target = active[index]
            if target.role != ROLE_USER:
                raise BadRequestError("Only user messages can be edited.")

            previous_agent_id: str | None = None
            for later in active[index + 1 :]:
                if later.role == ROLE_ASSISTANT:
                    try:
                        meta = json.loads(later.metadata_json or "{}")
                    except (ValueError, TypeError):
                        meta = {}
                    previous_agent_id = meta.get("agent_id") if isinstance(meta, dict) else None
                    break

            if target.edit_chain_id:
                chain_id = target.edit_chain_id
                version_stmt = (
                    select(MessageEditVersionEntity)
                    .where(MessageEditVersionEntity.chain_id == chain_id)
                    .order_by(MessageEditVersionEntity.version_number.desc())
                )
                latest = (await db.exec(version_stmt)).first()
                next_version = (latest.version_number if latest else 0) + 1
            else:
                chain_id = _new_id("editchain")
                db.add(
                    MessageEditChainEntity(
                        id=chain_id,
                        session_id=session_id,
                        tenant_id=context.tenant_id,
                        actor_id=context.actor_id,
                        anchor_position=str(index),
                        created_at=now,
                    )
                )
                original_version_id = _new_id("editver")
                original_assistant = next(
                    (message for message in active[index + 1 :] if message.role == ROLE_ASSISTANT),
                    None,
                )
                target.edit_chain_id = chain_id
                target.edit_version_id = original_version_id
                if original_assistant is not None:
                    original_assistant.edit_chain_id = chain_id
                    original_assistant.edit_version_id = original_version_id
                    db.add(original_assistant)
                db.add(target)
                db.add(
                    MessageEditVersionEntity(
                        id=original_version_id,
                        chain_id=chain_id,
                        version_number=1,
                        user_message_id=target.id,
                        assistant_message_id=(
                            original_assistant.id if original_assistant is not None else None
                        ),
                        created_at=now,
                    )
                )
                next_version = 2

            for stale in active[index:]:
                stale.is_superseded = True
                db.add(stale)

            version_id = _new_id("editver")
            new_message = ChatMessageEntity(
                id=_new_id("msg"),
                session_id=session_id,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                user_id=context.user_id,
                role=ROLE_USER,
                content=content,
                metadata_json="{}",
                edit_chain_id=chain_id,
                edit_version_id=version_id,
                created_at=now,
            )
            db.add(new_message)
            db.add(
                MessageEditVersionEntity(
                    id=version_id,
                    chain_id=chain_id,
                    version_number=next_version,
                    user_message_id=new_message.id,
                    assistant_message_id=None,
                    created_at=now,
                )
            )

            owned.updated_at = now
            db.add(owned)

            return EditedMessage(new_message=new_message, previous_agent_id=previous_agent_id)

    async def link_edit_version_assistant(
        self, *, version_id: str | None, assistant_message_id: str
    ) -> None:
        if not version_id:
            return
        async with self._connector.session() as db:
            stmt = select(MessageEditVersionEntity).where(
                MessageEditVersionEntity.id == version_id
            )
            version = (await db.exec(stmt)).first()
            if version is not None:
                version.assistant_message_id = assistant_message_id
                db.add(version)
                assistant_stmt = select(ChatMessageEntity).where(
                    ChatMessageEntity.id == assistant_message_id
                )
                assistant = (await db.exec(assistant_stmt)).first()
                if assistant is not None:
                    assistant.edit_chain_id = version.chain_id
                    assistant.edit_version_id = version.id
                    db.add(assistant)

    async def set_feedback(
        self,
        *,
        context: SessionContext,
        session_id: str,
        message_id: str,
        feedback: str | None,
    ) -> ChatMessageEntity:
        async with self._connector.session() as db:
            owned = await self._fetch_owned(db, context, session_id)
            if owned is None:
                raise NotFoundError(
                    "Chat session not found.", details={"session_id": session_id}
                )

            msg_stmt = select(ChatMessageEntity).where(
                ChatMessageEntity.id == message_id,
                ChatMessageEntity.tenant_id == context.tenant_id,
                ChatMessageEntity.session_id == session_id,
            )
            message = (await db.exec(msg_stmt)).first()
            if message is None:
                raise NotFoundError(
                    "Message not found.", details={"message_id": message_id}
                )

            message.feedback = feedback
            db.add(message)

            fb_stmt = select(MessageFeedbackEntity).where(
                MessageFeedbackEntity.message_id == message_id
            )
            existing = (await db.exec(fb_stmt)).first()
            if feedback is None:
                if existing is not None:
                    await db.delete(existing)
            elif existing is not None:
                existing.feedback = feedback
                db.add(existing)
            else:
                db.add(
                    MessageFeedbackEntity(
                        message_id=message_id,
                        session_id=session_id,
                        tenant_id=context.tenant_id,
                        actor_id=context.actor_id,
                        feedback=feedback,
                        source="chat_ui",
                        metadata_json="{}",
                    )
                )

            return message

    async def _load_owned_session(
        self, *, context: SessionContext, session_id: str
    ) -> ChatSessionEntity | None:
        async with self._connector.session() as session:
            return await self._fetch_owned(session, context, session_id)

    @staticmethod
    async def _fetch_owned(session, context: SessionContext, session_id: str):
        stmt = select(ChatSessionEntity).where(
            ChatSessionEntity.id == session_id,
            ChatSessionEntity.tenant_id == context.tenant_id,
            ChatSessionEntity.user_id == context.user_id,
            ChatSessionEntity.archived_at.is_(None),
        )
        return (await session.exec(stmt)).first()


_store: ConversationStore | None = None


def get_conversation_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store
