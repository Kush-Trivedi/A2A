"""Session-scoped uploads — the file agent's knowledge source.

Storing requires the caller to OWN the chat session (same ownership rule as
reading messages). Reading on the service plane filters by tenant + session
+ forwarded user, so an agent can only ever see documents the envelope's
user uploaded into the envelope's session. Re-uploading identical content
(same sha256) into a session is skipped, not duplicated."""

import uuid

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.chat import SessionDocumentEntity
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError
from ..chat.session_service import get_chat_session_service
from .file_upload_service import UploadPreparation

logger = Logger(__name__).get_logger()


class SessionDocumentService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def store(
        self,
        *,
        context: SessionContext,
        session_id: str,
        preparation: UploadPreparation,
    ) -> int:
        """Persist every prepared document against the session. Returns how
        many were stored (identical content already present is skipped)."""
        await get_chat_session_service().get_owned_session(
            context=context, session_id=session_id
        )
        try:
            async with self._db.session() as session:
                existing = set(
                    (
                        await session.exec(
                            select(SessionDocumentEntity.sha256).where(
                                SessionDocumentEntity.tenant_id == context.tenant_id,
                                SessionDocumentEntity.session_id == session_id,
                            )
                        )
                    ).all()
                )
                stored = 0
                for prepared in preparation.prepared:
                    if prepared.sha256 in existing:
                        continue
                    existing.add(prepared.sha256)
                    session.add(
                        SessionDocumentEntity(
                            id=uuid.uuid4().hex,
                            session_id=session_id,
                            tenant_id=context.tenant_id,
                            user_id=context.user_id,
                            upload_name=preparation.upload_name,
                            file_name=prepared.file_name,
                            sha256=prepared.sha256,
                            characters=prepared.characters,
                            content=prepared.text,
                        )
                    )
                    stored += 1
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        logger.info(
            "Session documents stored",
            extra={
                "session_id": session_id,
                "stored": stored,
                "skipped": len(preparation.prepared) - stored,
            },
        )
        return stored

    async def list_for_session(
        self, *, tenant_id: str, user_id: str, session_id: str
    ) -> list[SessionDocumentEntity]:
        """Service-plane read: every filter is mandatory — the forwarded
        user only sees what they uploaded into this session."""
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(SessionDocumentEntity)
                        .where(
                            SessionDocumentEntity.tenant_id == tenant_id,
                            SessionDocumentEntity.user_id == user_id,
                            SessionDocumentEntity.session_id == session_id,
                        )
                        .order_by(SessionDocumentEntity.created_at)  # type: ignore[arg-type]
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_service: SessionDocumentService | None = None


def get_session_document_service() -> SessionDocumentService:
    global _service
    if _service is None:
        _service = SessionDocumentService()
    return _service
