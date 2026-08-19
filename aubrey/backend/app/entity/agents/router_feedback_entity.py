from sqlalchemy import Column, Float, Index, Text
from sqlmodel import Field

from backend.app.entity.base_models import CreatedAtModel
from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class PgHalfVec(PgVector):
    """Half-precision pgvector column. Same bind/result behavior as
    PgVector ('[..]' literals in, float lists out); only the column type
    differs — halfvec halves storage and cosine `<=>` works natively."""

    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return f"halfvec({self.dimension})"


class FeedbackSignal:
    POSITIVE = "positive"
    NEGATIVE = "negative"


class FeedbackSource:
    """What produced the signal — an ordinary answer, a negative-marker
    (not_supported/no_data) answer, or explicit user feedback."""

    ANSWER = "answer"
    NOT_SUPPORTED = "not_supported"
    FEEDBACK = "feedback"


class RouterFeedbackEntity(CreatedAtModel, table=True):
    """Router learning ledger (NEW_PLAN §5 `router_feedback`, M10d T3).

    One append-only row per routing outcome: the question's embedding, the
    agent that answered, and whether the outcome looked right ("positive")
    or wrong ("negative"). The router folds these into candidate scores as
    a decayed similarity mass (router_learning_service) — positive rows
    behave exactly like mined utterances, so this table IS the utterance
    mine and agent_routes is never rewritten (append+decay, principle 4).

    Index choice — deliberately NO HNSW on question_embedding: the scoring
    query scans only the newest `agents.router.feedback_recent_n` rows per
    tenant and computes cosine in plain SQL. At that scale a sequential
    scan beats maintaining an ANN index (and full-precision HNSW is capped
    at 2000 dims anyway, which is also why the column is halfvec).
    Revisit only if feedback volume outgrows the recent-N window."""

    __tablename__ = "router_feedback"
    __table_args__ = (
        Index("idx_router_feedback_tenant_created", "tenant_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    question_embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgHalfVec(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    signal: str = Field(sa_column=Column(Text, nullable=False))  # positive | negative
    weight: float = Field(default=1.0, sa_column=Column(Float, nullable=False, default=1.0))
    source: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
