"""Question → agent, with zero intent classification and zero LLM.

The signal is the registered utterance index: dense cosine (cloud) or
full-text rank (local/credential-less), per yaml agents.router.mode. The
decision ladder:

    no candidates                    -> FALLBACK (yaml fallback_agent)
    sticky agent close enough        -> stay with it (switch needs margin)
    top score under the floor        -> FALLBACK
    top agent not permitted (Casbin) -> REFUSAL_INACCESSIBLE (named team)
    top two too close                -> DISAMBIGUATE
    else                             -> DISPATCH
"""

from dataclasses import dataclass, field

from sqlalchemy import text as sql_text

from ...database.rdbms.pg_session import get_postgres_connector
from ...security.authorization.enforcer import get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError
from .route_index_service import RouterSettings, _vector_literal, get_router_settings

logger = Logger(__name__).get_logger()


class RouteAction:
    DISPATCH = "dispatch"
    DISAMBIGUATE = "disambiguate"
    REFUSAL_INACCESSIBLE = "refusal_inaccessible"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class RouteCandidate:
    agent_key: str
    display_name: str
    team_key: str
    permission: str
    card_url: str
    score: float


@dataclass(frozen=True)
class RouteDecision:
    action: str
    mode: str
    agent_key: str = ""
    matched: RouteCandidate | None = None
    candidates: tuple[RouteCandidate, ...] = field(default_factory=tuple)


_DENSE_SQL = """
SELECT r.agent_key, a.display_name, a.permission, COALESCE(a.card_url, '') AS card_url,
       t.key AS team_key,
       MAX(1 - (r.embedding <=> CAST(:vec AS vector))) AS score
FROM agent_routes r
JOIN registered_agents a
  ON a.agent_key = r.agent_key AND a.tenant_id = r.tenant_id
JOIN odt_teams t ON t.id = a.team_id
WHERE r.tenant_id = :tenant_id AND a.status = 'active' AND r.embedding IS NOT NULL
GROUP BY r.agent_key, a.display_name, a.permission, a.card_url, t.key
ORDER BY score DESC
LIMIT 5
"""

_SPARSE_SQL = """
WITH q AS (
    SELECT regexp_replace(
        plainto_tsquery('english', :question)::text, '&', '|', 'g'
    )::tsquery AS tsq
)
SELECT r.agent_key, a.display_name, a.permission, COALESCE(a.card_url, '') AS card_url,
       t.key AS team_key,
       MAX(ts_rank(r.search_vector, q.tsq)) AS score
FROM agent_routes r
JOIN registered_agents a
  ON a.agent_key = r.agent_key AND a.tenant_id = r.tenant_id
JOIN odt_teams t ON t.id = a.team_id
CROSS JOIN q
WHERE r.tenant_id = :tenant_id AND a.status = 'active' AND r.search_vector @@ q.tsq
GROUP BY r.agent_key, a.display_name, a.permission, a.card_url, t.key
ORDER BY score DESC
LIMIT 5
"""


class QuestionRouterService:
    def __init__(self, settings: RouterSettings | None = None) -> None:
        self._db = get_postgres_connector()
        self._settings = settings or get_router_settings()

    async def route(
        self,
        *,
        context: SessionContext,
        question: str,
        sticky_agent: str | None = None,
        requested_agent: str | None = None,
    ) -> RouteDecision:
        mode = self._settings.mode
        candidates = await self._score(context.tenant_id, question, mode)

        if requested_agent:
            # The user pinned an agent explicitly — respect it if permitted.
            pinned = next(
                (c for c in candidates if c.agent_key == requested_agent), None
            ) or await self._candidate_for(context.tenant_id, requested_agent)
            if pinned is not None:
                if await self._permitted(context, pinned):
                    return RouteDecision(
                        action=RouteAction.DISPATCH, mode=mode,
                        agent_key=pinned.agent_key, matched=pinned,
                        candidates=tuple(candidates),
                    )
                return RouteDecision(
                    action=RouteAction.REFUSAL_INACCESSIBLE, mode=mode,
                    matched=pinned, candidates=tuple(candidates),
                )

        if not candidates:
            return await self._fallback(context, mode)

        threshold = (
            self._settings.threshold if mode == "dense" else self._settings.sparse_threshold
        )
        top = candidates[0]

        if sticky_agent and top.agent_key != sticky_agent:
            sticky_score = next(
                (c.score for c in candidates if c.agent_key == sticky_agent), 0.0
            )
            must_switch = (
                top.score >= threshold
                and (top.score - sticky_score) >= self._settings.switch_margin
            )
            if not must_switch:
                sticky = await self._candidate_for(context.tenant_id, sticky_agent)
                if sticky is not None and await self._permitted(context, sticky):
                    return RouteDecision(
                        action=RouteAction.DISPATCH, mode=mode,
                        agent_key=sticky.agent_key, matched=sticky,
                        candidates=tuple(candidates),
                    )

        if top.score < threshold:
            return await self._fallback(context, mode, candidates)

        if not await self._permitted(context, top):
            return RouteDecision(
                action=RouteAction.REFUSAL_INACCESSIBLE, mode=mode,
                matched=top, candidates=tuple(candidates),
            )

        if len(candidates) > 1:
            runner_up = candidates[1]
            ambiguous = (
                (top.score - runner_up.score) < self._settings.margin
                if mode == "dense"
                else runner_up.score >= top.score * self._settings.sparse_ambiguity_ratio
            )
            if ambiguous and await self._permitted(context, runner_up):
                return RouteDecision(
                    action=RouteAction.DISAMBIGUATE, mode=mode,
                    candidates=(top, runner_up),
                )

        return RouteDecision(
            action=RouteAction.DISPATCH, mode=mode,
            agent_key=top.agent_key, matched=top, candidates=tuple(candidates),
        )

    async def _score(
        self, tenant_id: str, question: str, mode: str
    ) -> list[RouteCandidate]:
        params: dict = {"tenant_id": tenant_id}
        if mode == "dense":
            from ..knowledge import get_embedding_service

            vector = (await get_embedding_service().embed([question]))[0]
            statement, params["vec"] = sql_text(_DENSE_SQL), _vector_literal(vector)
        else:
            statement, params["question"] = sql_text(_SPARSE_SQL), question
        try:
            async with self._db.session() as session:
                rows = (await session.execute(statement, params)).mappings().all()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        return [
            RouteCandidate(
                agent_key=row["agent_key"],
                display_name=row["display_name"],
                team_key=row["team_key"],
                permission=row["permission"] or "",
                card_url=row["card_url"] or "",
                score=float(row["score"] or 0.0),
            )
            for row in rows
        ]

    async def _candidate_for(
        self, tenant_id: str, agent_key: str
    ) -> RouteCandidate | None:
        statement = sql_text(
            """
            SELECT a.agent_key, a.display_name, a.permission,
                   COALESCE(a.card_url, '') AS card_url, t.key AS team_key
            FROM registered_agents a JOIN odt_teams t ON t.id = a.team_id
            WHERE a.tenant_id = :tenant_id AND a.agent_key = :agent_key
              AND a.status = 'active'
            """
        )
        try:
            async with self._db.session() as session:
                row = (
                    await session.execute(
                        statement,
                        {"tenant_id": tenant_id, "agent_key": agent_key.strip().lower()},
                    )
                ).mappings().first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if row is None:
            return None
        return RouteCandidate(
            agent_key=row["agent_key"],
            display_name=row["display_name"],
            team_key=row["team_key"],
            permission=row["permission"] or "",
            card_url=row["card_url"] or "",
            score=0.0,
        )

    async def _permitted(
        self, context: SessionContext, candidate: RouteCandidate
    ) -> bool:
        if not candidate.permission:
            return True
        if not context.roles:
            return False
        return await get_casbin_enforcer().enforce_any_role(
            context.roles,
            context.tenant_id,
            f"agent:{candidate.agent_key}",
            candidate.permission,
        )

    async def _fallback(
        self,
        context: SessionContext,
        mode: str,
        candidates: list[RouteCandidate] | None = None,
    ) -> RouteDecision:
        fallback = await self._candidate_for(
            context.tenant_id, self._settings.fallback_agent
        )
        if fallback is not None and await self._permitted(context, fallback):
            return RouteDecision(
                action=RouteAction.FALLBACK, mode=mode,
                agent_key=fallback.agent_key, matched=fallback,
                candidates=tuple(candidates or ()),
            )
        return RouteDecision(
            action=RouteAction.REFUSAL_INACCESSIBLE, mode=mode,
            candidates=tuple(candidates or ()),
        )


_service: QuestionRouterService | None = None


def get_question_router_service() -> QuestionRouterService:
    global _service
    if _service is None:
        _service = QuestionRouterService()
    return _service
