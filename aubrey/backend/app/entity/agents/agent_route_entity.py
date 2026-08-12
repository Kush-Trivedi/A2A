from sqlalchemy import Column, Computed, Index, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel
from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class AgentRouteEntity(TimestampModel, table=True):
    """The router's index: one row per routable utterance, harvested from
    what the team registers (description + skill descriptions + examples).
    Dense (embedding) and sparse (generated tsvector) signals come from the
    SAME rows — sparse routing needs zero model credentials."""

    __tablename__ = "agent_routes"
    __table_args__ = (
        Index("idx_agent_routes_tenant_agent", "tenant_id", "agent_key"),
        Index(
            "idx_agent_routes_search_vector", "search_vector", postgresql_using="gin"
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    utterance: str = Field(sa_column=Column(Text, nullable=False))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(
            TSVECTOR,
            Computed("to_tsvector('english', utterance)", persisted=True),
            nullable=True,
        ),
    )
