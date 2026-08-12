from sqlalchemy import Column, ForeignKey, Index, Integer, Text
from sqlmodel import Field

from backend.app.entity.base_models import CreatedAtModel


class SessionDocumentEntity(CreatedAtModel, table=True):
    """Prepared text of a file uploaded INTO a chat session. This is the
    file agent's only knowledge source: scoped to (tenant, user, session),
    never shared across sessions, and deleted with the session (CASCADE) —
    which is what keeps "answer only from this file" airtight."""

    __tablename__ = "session_documents"
    __table_args__ = (
        Index(
            "idx_session_documents_session",
            "tenant_id",
            "session_id",
            "created_at",
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    session_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        )
    )
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    upload_name: str = Field(sa_column=Column(Text, nullable=False))
    file_name: str = Field(sa_column=Column(Text, nullable=False))
    sha256: str = Field(sa_column=Column(Text, nullable=False))
    characters: int = Field(sa_column=Column(Integer, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
