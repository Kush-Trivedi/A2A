from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import text as sql_text

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...security.authorization.context_attrs import AuthorizationContextBuilder
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.session import SessionContext
from ...services.embedding.embedding_service import EmbeddingService, get_embedding_service
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()


class RouteAction(str, Enum):
    DISPATCH = "dispatch"
    DISAMBIGUATE = "disambiguate"
    REFUSAL_INACCESSIBLE = "refusal_inaccessible"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class RouteCandidate:
    agent_key: str
    display_name: str
    score: float


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    agent_key: str | None = None
    matched_agent: str | None = None
    candidates: list[RouteCandidate] = field(default_factory=list)
    mode: str = ""


class QuestionRouterService:
    """Supervisor-free direct dispatch: ONE embedding + ONE pgvector lookup
    (or a ~5ms sparse FTS query before LLM creds exist). No generation
    tokens, no router agent, no retraining — the index is maintained by
    agent registration. Routing SELECTS; Casbin still DECIDES."""

    # Sparse FTS scores are not calibrated like cosine similarity; a candidate
    # is "ambiguous" when the runner-up reaches this fraction of the top score.
    _SPARSE_AMBIGUITY_RATIO = 0.8

    def __init__(
        self,
        embedding: EmbeddingService | None = None,
        enforcer: CasbinEnforcer | None = None,
    ) -> None:
        self._connector = get_postgres_connector()
        self._embedding = embedding or get_embedding_service()
        self._enforcer = enforcer or get_casbin_enforcer()

    @staticmethod
    def _settings() -> dict:
        return get_application_context().agents.get("router", {}) or {}

    @property
    def fallback_agent(self) -> str:
        return str(self._settings().get("fallback_agent") or "general")

    async def route(
        self,
        *,
        context: SessionContext,
        question: str,
        sticky_agent: str | None = None,
    ) -> RouteDecision:
        settings = self._settings()
        threshold = float(settings.get("threshold") or 0.42)
        margin = float(settings.get("margin") or 0.06)
        switch_margin = float(settings.get("switch_margin") or 0.12)
        mode = str(settings.get("mode") or "auto")

        candidates, used_mode = await self._score(
            tenant_id=context.tenant_id, question=question, mode=mode
        )
        if used_mode == "sparse":
            threshold = float(settings.get("sparse_threshold") or 0.03)

        if not candidates:
            return RouteDecision(
                action=RouteAction.FALLBACK, agent_key=self.fallback_agent, mode=used_mode
            )

        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        scores = {c.agent_key: c.score for c in candidates}

        # Session stickiness: a mid-conversation follow-up rarely carries the
        # domain keywords — stay with the current agent unless a different
        # agent wins clearly (threshold + switch_margin over the sticky score).
        if sticky_agent and top.agent_key != sticky_agent:
            sticky_score = scores.get(sticky_agent, 0.0)
            if top.score < threshold or (top.score - sticky_score) < switch_margin:
                return RouteDecision(
                    action=RouteAction.DISPATCH, agent_key=sticky_agent, mode=used_mode
                )

        if top.score < threshold:
            return RouteDecision(
                action=RouteAction.FALLBACK, agent_key=self.fallback_agent, mode=used_mode
            )

        top_accessible = await self._accessible(context, top.agent_key)
        if not top_accessible:
            # Best match is an agent this user cannot use: the SPECIFIC
            # refusal ("this looks like X — contact team Y"), never a 403.
            return RouteDecision(
                action=RouteAction.REFUSAL_INACCESSIBLE,
                matched_agent=top.agent_key,
                mode=used_mode,
            )

        if runner_up is not None:
            ambiguous = (
                (top.score - runner_up.score) < margin
                if used_mode == "dense"
                else runner_up.score >= top.score * self._SPARSE_AMBIGUITY_RATIO
            )
            if ambiguous and await self._accessible(context, runner_up.agent_key):
                return RouteDecision(
                    action=RouteAction.DISAMBIGUATE,
                    candidates=[top, runner_up],
                    mode=used_mode,
                )

        return RouteDecision(
            action=RouteAction.DISPATCH, agent_key=top.agent_key, mode=used_mode
        )

    async def _score(
        self, *, tenant_id: str, question: str, mode: str
    ) -> tuple[list[RouteCandidate], str]:
        if mode in ("auto", "dense"):
            try:
                vector = await self._embedding.embed_query(question)
                if vector:
                    return await self._dense(tenant_id, vector), "dense"
            except Exception:  # noqa: BLE001 — degrade to sparse, one code path
                if mode == "dense":
                    raise
        return await self._sparse(tenant_id, question), "sparse"

    async def _dense(self, tenant_id: str, vector: list[float]) -> list[RouteCandidate]:
        vector_literal = "[" + ",".join(str(float(x)) for x in vector) + "]"
        async with self._connector.session() as session:
            rows = (
                await session.execute(
                    sql_text(
                        "SELECT r.agent_key, a.display_name, "
                        "MAX(1 - (r.embedding <=> CAST(:vec AS vector))) AS score "
                        "FROM agent_routes r "
                        "JOIN registered_agents a "
                        "ON a.agent_key = r.agent_key AND a.tenant_id = r.tenant_id "
                        "WHERE r.tenant_id = :tenant AND a.status = 'active' "
                        "AND r.embedding IS NOT NULL "
                        "GROUP BY r.agent_key, a.display_name "
                        "ORDER BY score DESC LIMIT 5"
                    ),
                    {"vec": vector_literal, "tenant": tenant_id},
                )
            ).all()
        return [
            RouteCandidate(
                agent_key=str(row.agent_key),
                display_name=str(row.display_name),
                score=float(row.score),
            )
            for row in rows
        ]

    async def _sparse(self, tenant_id: str, question: str) -> list[RouteCandidate]:
        async with self._connector.session() as session:
            rows = (
                await session.execute(
                    sql_text(
                        "WITH q AS (SELECT regexp_replace("
                        "plainto_tsquery('english', :question)::text, '&', '|', 'g'"
                        ")::tsquery AS query) "
                        "SELECT r.agent_key, a.display_name, "
                        "MAX(ts_rank(r.search_vector, q.query)) AS score "
                        "FROM agent_routes r "
                        "JOIN registered_agents a "
                        "ON a.agent_key = r.agent_key AND a.tenant_id = r.tenant_id "
                        "CROSS JOIN q "
                        "WHERE r.tenant_id = :tenant AND a.status = 'active' "
                        "AND r.search_vector @@ q.query "
                        "GROUP BY r.agent_key, a.display_name "
                        "ORDER BY score DESC LIMIT 5"
                    ),
                    {"question": question, "tenant": tenant_id},
                )
            ).all()
        return [
            RouteCandidate(
                agent_key=str(row.agent_key),
                display_name=str(row.display_name),
                score=float(row.score),
            )
            for row in rows
        ]

    async def _accessible(self, context: SessionContext, agent_key: str) -> bool:
        if not self._enforcer.enabled:
            return True
        return await self._enforcer.enforce_any_role(
            context.roles,
            context.tenant_id,
            f"agent:{agent_key}",
            "chat",
            AuthorizationContextBuilder.build(context),
        )


_service: QuestionRouterService | None = None


def get_question_router_service() -> QuestionRouterService:
    global _service
    if _service is None:
        _service = QuestionRouterService()
    return _service
