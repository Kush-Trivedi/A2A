from sqlalchemy import Column, ForeignKey, Index, Text, UniqueConstraint
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class DocumentGrantEntity(TimestampModel, table=True):
    """Ownership link: 'this team + agent may use this document'. Content is
    stored (and later embedded) once in `documents`; sharing the same file
    with another agent or team is one row here, never a re-ingest."""

    __tablename__ = "document_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_id", "team_key", "agent_key",
            name="uq_document_grants_owner",
        ),
        Index("idx_document_grants_agent", "tenant_id", "agent_key"),
        Index("idx_document_grants_document", "document_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    document_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
    )
    team_key: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
