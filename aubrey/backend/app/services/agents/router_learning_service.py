"""Router learning (NEW_PLAN M10d — §4 T2/T3, §5 router_feedback).

The routing index learns from outcomes without anyone labeling anything:

- **Signals.** After an agent's ANSWER persists, the conversation hook
  fires `record_outcome_background`: the answer is a positive signal for
  (question → agent) unless it *begins* with a negative marker — the
  distinctive stems of the agents' not_supported / no_data templates
  (yaml `agents.router.negative_markers`, case-insensitive prefix match) —
  in which case it is a negative signal. Explicit user feedback can call
  `record_positive` / `record_negative` with source="feedback".

- **Scoring.** `feedback_adjustments` returns a per-agent additive score
  adjustment: `feedback_weight * (positive mass - negative mass)`, clamped
  to ±`feedback_cap`. Each row contributes
  `cosine_similarity * weight * 0.5 ** (age_days / half_life_days)` and
  rows under `feedback_min_similarity` contribute nothing. The SQL scans
  only the newest `feedback_recent_n` rows per tenant — a plain sequential
  scan with cosine computed inline, no ANN index (documented on the
  entity): at this scale the scan is cheaper than index maintenance.
  Dense mode only — sparse ts_rank scores are not on the cosine scale the
  adjustment is calibrated for, so sparse routing stays unadjusted.

- **Utterance mining = this table.** Mining a positively-answered question
  into agent_routes would add one dense row that boosts that agent for
  similar future questions. A `record_positive` row already does exactly
  that through the feedback adjustment — same embedding, same cosine,
  plus decay — WITHOUT mutating the team-declared route index. So the
  positive rows ARE the mined utterances (with the decay NEW_PLAN §5
  requires), and there is no separate mine_utterance writer: append and
  decay, never rewrite (principle 4).

Everything here is fail-soft: recording and scoring errors are logged and
swallowed — routing never breaks because learning did."""

import asyncio
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import text as sql_text

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import FeedbackSignal, FeedbackSource, RouterFeedbackEntity
from ...entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS
from ...utils.common.logger import Logger
from .route_index_service import _vector_literal

logger = Logger(__name__).get_logger()

_DEFAULT_JUDGE_PROMPT = (
    "You are the routing judge for a multi-agent assistant. The scored "
    "candidates for the user's question are too close to call. Pick the "
    "ONE agent best suited to answer, or ASK if only the user can "
    "resolve it.\n\nCandidates (key: what the agent covers):\n"
    "{candidates}\n\nQuestion: {question}\n\n"
    "Reply with EXACTLY one token: the chosen agent key, or ASK."
)

# Distinctive stems of the agents' not_supported / no_data reply templates
# (see Agents/*/agent.yaml). Yaml `agents.router.negative_markers` overrides.
_DEFAULT_NEGATIVE_MARKERS = (
    "the data source returned no rows",
    "the contract data returned no rows",
    "that question is outside",
)


@dataclass(frozen=True)
class RouterLearningSettings:
    judge_enabled: bool
    judge_band: float
    judge_prompt: str
    judge_max_output_tokens: int
    negative_markers: tuple[str, ...]
    feedback_weight: float
    feedback_min_similarity: float
    feedback_half_life_days: float
    feedback_cap: float
    feedback_recent_n: int
    sticky_decay_per_turn: float
    thresholds_per_agent: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def get_router_learning_settings() -> RouterLearningSettings:
    cfg = get_application_context().agents.get("router") or {}
    markers = tuple(
        str(m).strip() for m in (cfg.get("negative_markers") or _DEFAULT_NEGATIVE_MARKERS)
        if str(m).strip()
    )
    thresholds = {
        str(key).strip().lower(): float(value)
        for key, value in (cfg.get("thresholds_per_agent") or {}).items()
    }
    return RouterLearningSettings(
        judge_enabled=bool(cfg.get("judge_enabled", False)),
        judge_band=float(cfg.get("judge_band", 0.08)),
        judge_prompt=str(cfg.get("judge_prompt") or _DEFAULT_JUDGE_PROMPT),
        judge_max_output_tokens=int(cfg.get("judge_max_output_tokens", 16)),
        negative_markers=markers,
        feedback_weight=float(cfg.get("feedback_weight", 0.1)),
        feedback_min_similarity=float(cfg.get("feedback_min_similarity", 0.78)),
        feedback_half_life_days=float(cfg.get("feedback_half_life_days", 30)),
        feedback_cap=float(cfg.get("feedback_cap", 0.15)),
        feedback_recent_n=int(cfg.get("feedback_recent_n", 500)),
        sticky_decay_per_turn=float(cfg.get("sticky_decay_per_turn", 0.02)),
        thresholds_per_agent=thresholds,
    )


def decayed_weight(age_days: float, half_life_days: float) -> float:
    """Pure mirror of the SQL decay term: 0.5 ** (age_days / half_life).
    A non-positive half-life disables decay rather than exploding."""
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def is_negative_answer(answer: str, markers: tuple[str, ...]) -> bool:
    """Cheap negative detection: does the answer BEGIN with any marker
    stem (case-insensitive prefix)? Markers are yaml-owned."""
    stripped = (answer or "").lstrip().lower()
    if not stripped:
        return False
    return any(stripped.startswith(m.lower()) for m in markers if m)


def parse_judge_reply(reply: str, candidate_keys: list[str]) -> str | None:
    """Strict-token parse of the judge's reply.

    Returns the matched agent_key, the sentinel "ask" for an ASK verdict,
    or None when the reply is garbage — callers treat "ask"/None the same
    (fall through to the scored decision) but log them differently."""
    token = (reply or "").strip().splitlines()[0].strip() if (reply or "").strip() else ""
    token = token.strip("\"'`.,:; ").lower()
    if not token:
        return None
    if token == "ask":
        return "ask"
    lowered = {key.lower(): key for key in candidate_keys}
    return lowered.get(token)


_FEEDBACK_MASS_SQL = f"""
WITH recent AS (
    SELECT agent_key, signal, weight, created_at,
           1 - (question_embedding <=> CAST(:vec AS halfvec({DEFAULT_EMBEDDING_DIMENSIONS}))) AS sim
    FROM router_feedback
    WHERE tenant_id = :tenant_id AND question_embedding IS NOT NULL
    ORDER BY created_at DESC
    LIMIT :recent_n
)
SELECT agent_key, signal,
       SUM(sim * weight * POWER(
           0.5,
           GREATEST(EXTRACT(EPOCH FROM (NOW() - created_at)), 0) / :half_life_seconds
       )) AS mass
FROM recent
WHERE sim >= :min_similarity
GROUP BY agent_key, signal
"""


class RouterLearningService:
    def __init__(self, settings: RouterLearningSettings | None = None) -> None:
        self._db = get_postgres_connector()
        self._settings = settings or get_router_learning_settings()
        self._background: set[asyncio.Task] = set()

    # -- write side (signals) ---------------------------------------------- #

    def record_outcome_background(
        self, *, tenant_id: str, question: str, answer: str, agent_key: str
    ) -> None:
        """The one conversation hook: classify the persisted answer and
        record the signal fire-and-forget (held reference — bare
        create_task results are GC-collectable mid-flight)."""
        task = asyncio.create_task(
            self._record_outcome(
                tenant_id=tenant_id, question=question,
                answer=answer, agent_key=agent_key,
            )
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _record_outcome(
        self, *, tenant_id: str, question: str, answer: str, agent_key: str
    ) -> None:
        try:
            if is_negative_answer(answer, self._settings.negative_markers):
                await self.record_negative(
                    tenant_id=tenant_id, question=question, agent_key=agent_key,
                    source=FeedbackSource.NOT_SUPPORTED,
                )
            else:
                await self.record_positive(
                    tenant_id=tenant_id, question=question, agent_key=agent_key,
                    source=FeedbackSource.ANSWER,
                )
        except Exception:  # noqa: BLE001 — background: log, never raise
            logger.warning("Router feedback recording failed", exc_info=True)

    async def record_positive(
        self, *, tenant_id: str, question: str, agent_key: str,
        source: str = FeedbackSource.ANSWER, weight: float = 1.0,
    ) -> None:
        await self._record(
            tenant_id=tenant_id, question=question, agent_key=agent_key,
            signal=FeedbackSignal.POSITIVE, source=source, weight=weight,
        )

    async def record_negative(
        self, *, tenant_id: str, question: str, agent_key: str,
        source: str = FeedbackSource.NOT_SUPPORTED, weight: float = 1.0,
    ) -> None:
        await self._record(
            tenant_id=tenant_id, question=question, agent_key=agent_key,
            signal=FeedbackSignal.NEGATIVE, source=source, weight=weight,
        )

    async def _record(
        self, *, tenant_id: str, question: str, agent_key: str,
        signal: str, source: str, weight: float,
    ) -> None:
        embedding: list[float] | None = None
        try:
            from ..knowledge import get_embedding_service

            embedding = (await get_embedding_service().embed([question]))[0]
        except Exception:  # noqa: BLE001 — a vectorless row scores nothing but keeps the audit trail
            logger.warning(
                "Feedback embedding failed — recording signal without a vector",
                exc_info=True,
            )
        async with self._db.session() as session:
            session.add(
                RouterFeedbackEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    question_embedding=embedding,
                    agent_key=agent_key,
                    signal=signal,
                    weight=float(weight),
                    source=source,
                )
            )
        logger.info(
            "Router feedback recorded",
            extra={"agent_key": agent_key, "signal": signal, "source": source},
        )

    # -- read side (scoring) ----------------------------------------------- #

    async def feedback_adjustments(
        self, *, tenant_id: str, question_vector: list[float]
    ) -> dict[str, float]:
        """Per-agent additive score adjustment for THIS question, from one
        SQL aggregate over the recent feedback window. Reuses the query
        embedding the router already computed — never embeds again.
        Fail-soft: any error returns {} (no adjustment)."""
        settings = self._settings
        if settings.feedback_weight <= 0 or not question_vector:
            return {}
        try:
            params = {
                "tenant_id": tenant_id,
                "vec": _vector_literal(question_vector),
                "recent_n": settings.feedback_recent_n,
                "min_similarity": settings.feedback_min_similarity,
                "half_life_seconds": max(settings.feedback_half_life_days, 0.001) * 86400.0,
            }
            async with self._db.session() as session:
                rows = (
                    await session.execute(sql_text(_FEEDBACK_MASS_SQL), params)
                ).mappings().all()
        except Exception:  # noqa: BLE001 — learning never breaks routing
            logger.warning("Feedback adjustment query failed — no adjustment", exc_info=True)
            return {}

        masses: dict[str, float] = {}
        for row in rows:
            mass = float(row["mass"] or 0.0)
            if row["signal"] == FeedbackSignal.NEGATIVE:
                mass = -mass
            masses[row["agent_key"]] = masses.get(row["agent_key"], 0.0) + mass

        cap = settings.feedback_cap
        adjustments = {
            agent_key: max(-cap, min(cap, settings.feedback_weight * mass))
            for agent_key, mass in masses.items()
        }
        return {key: value for key, value in adjustments.items() if value != 0.0}


_service: RouterLearningService | None = None


def get_router_learning_service() -> RouterLearningService:
    global _service
    if _service is None:
        _service = RouterLearningService()
    return _service
