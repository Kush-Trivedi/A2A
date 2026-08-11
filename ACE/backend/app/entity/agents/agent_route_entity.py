from sqlmodel import Field
from sqlalchemy import Column, Computed, Index, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from backend.app.entity.base_models import CreatedAtModel
from backend.app.entity.pgvector.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class AgentRouteEntity(CreatedAtModel, table=True):
    """The question-router index: one row per route utterance per agent.

    Utterances come from the agent's card (description + skill descriptions +
    skill examples) at registration — teams sharpen routing by editing their
    own manifest, never ACE. `embedding` is filled when the embedding
    deployment is configured (dense routing); the computed tsvector powers
    the sparse degraded mode so routing works in every environment.
    """

    __tablename__ = "agent_routes"
    __table_args__ = (
        Index("idx_agent_routes_tenant_agent", "tenant_id", "agent_key"),
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
