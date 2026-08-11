from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel


class KnowledgeSourceEntity(TimestampModel, table=True):
    """The source registry: who owns each knowledge source, which agents may
    retrieve from it, and which user roles may read results.

    Three-way access map recorded at ingestion time:
      owner team  ->  bound agents  ->  reader roles
    The retrieve capability enforces the agent binding; Casbin enforces the
    role read (`knowledge:<source> read`, seeded from `roles`).
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_name", name="uq_knowledge_sources_tenant_name"),
        Index("idx_knowledge_sources_tenant", "tenant_id"),
        Index("idx_knowledge_sources_team", "tenant_id", "owner_team_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    source_name: str = Field(sa_column=Column(Text, nullable=False))
    owner_team_key: str = Field(sa_column=Column(Text, nullable=False))
    connection_name: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    status: str = Field(default="active", sa_column=Column(Text, nullable=False, default="active"))
    location: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    chunking: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    embedding: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    agents: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    roles: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
