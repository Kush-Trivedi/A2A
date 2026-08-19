"""Question → agent, scored first, LLM only on the edge cases.

The signal is the registered utterance index: dense cosine (cloud) or
full-text rank (local/credential-less), per yaml agents.router.mode.
Dense scores are adjusted by decayed routing feedback (M10d — see
router_learning_service; sparse skips the adjustment because ts_rank is
not on the cosine scale it is calibrated for). The decision ladder:

    no candidates                    -> FALLBACK (yaml fallback_agent)
    sticky agent close enough        -> stay with it (switch needs margin,
                                        which decays by turns since the
                                        sticky agent last answered)
    top score under the floor        -> judge if within judge_band, else FALLBACK
    top agent not permitted (Casbin) -> REFUSAL_INACCESSIBLE (named team)
    top two too close / near band    -> LLM router-judge picks an agent or ASK
                                        (fail-open to the scored decision)
    top two too close, judge passed  -> DISAMBIGUATE
    else                             -> DISPATCH

Per-agent calibrated dispatch floors: yaml agents.router.thresholds_per_agent
(dense cosine scale) overrides the global threshold per agent_key; empty by
default. Calibration is manual/config for now — every decision logs
agent_key + score + action at info level so calibration data accumulates
in logs."""

from dataclasses import dataclass, field, replace

from sqlalchemy import text as sql_text

from ...database.rdbms.pg_session import get_postgres_connector
from ...security.authorization.enforcer import get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError
from .route_index_service import RouterSettings, _vector_literal, get_router_settings
from .router_learning_service import (
    RouterLearningSettings,
    get_router_learning_service,
    get_router_learning_settings,
    parse_judge_reply,
)

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
    def __init__(
        self,
        settings: RouterSettings | None = None,
        learning: RouterLearningSettings | None = None,
    ) -> None:
        self._db = get_postgres_connector()
        self._settings = settings or get_router_settings()
        self._learning = learning or get_router_learning_settings()

    async def route(
        self,
        *,
        context: SessionContext,
        question: str,
        sticky_agent: str | None = None,
        requested_agent: str | None = None,
        turns_since_sticky: int = 0,
    ) -> RouteDecision:
        mode = self._settings.mode
        candidates = await self._score(context.tenant_id, question, mode)
        # The fallback agent is a safety net, never a contestant: its
        # question-shaped utterances otherwise siphon wins from domain
        # agents (NEW_PLAN M10a).
        if self._settings.fallback_agent:
            candidates = [
                c for c in candidates if c.agent_key != self._settings.fallback_agent
            ]

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
                return await self._fallback(context, mode, denied_candidates=pinned)

        if not candidates:
            return await self._fallback(context, mode)

        top = candidates[0]
        threshold = self._threshold_for(top.agent_key, mode)

        if sticky_agent and top.agent_key != sticky_agent:
            sticky_score = next(
                (c.score for c in candidates if c.agent_key == sticky_agent), 0.0
            )
            # M10d T3: sticky influence weakens with distance — the margin a
            # challenger must clear shrinks per turn since the sticky agent
            # last answered (turns_since_sticky=0 keeps the old behavior).
            required_margin = max(
                0.0,
                self._settings.switch_margin
                - self._learning.sticky_decay_per_turn * max(0, turns_since_sticky),
            )
            must_switch = (
                top.score >= threshold
                and (top.score - sticky_score) >= required_margin
            )
            if not must_switch:
                sticky = await self._candidate_for(context.tenant_id, sticky_agent)
                if sticky is not None and await self._permitted(context, sticky):
                    self._log_decision(
                        RouteAction.DISPATCH, mode, sticky.agent_key, sticky_score,
                        threshold, reason="sticky",
                    )
                    return RouteDecision(
                        action=RouteAction.DISPATCH, mode=mode,
                        agent_key=sticky.agent_key, matched=sticky,
                        candidates=tuple(candidates),
                    )

        if top.score < threshold:
            # M10d T2: a near-miss under the floor gets one judge look
            # before falling back (dense only — the band is cosine-scale).
            if mode == "dense" and (threshold - top.score) <= self._learning.judge_band:
                judged = await self._judge(
                    context, question, candidates, reason="below_floor"
                )
                if judged is not None:
                    self._log_decision(
                        RouteAction.DISPATCH, mode, judged.agent_key, judged.score,
                        threshold, reason="judge_below_floor",
                    )
                    return RouteDecision(
                        action=RouteAction.DISPATCH, mode=mode,
                        agent_key=judged.agent_key, matched=judged,
                        candidates=tuple(candidates),
                    )
            self._log_decision(
                RouteAction.FALLBACK, mode, top.agent_key, top.score,
                threshold, reason="below_floor",
            )
            return await self._fallback(context, mode, candidates=candidates, denied_candidates=top)
        if not await self._permitted(context, top):
            return await self._fallback(context, mode, candidates=candidates, denied_candidates=top)

        runner_up = candidates[1] if len(candidates) > 1 else None
        ambiguous = runner_up is not None and (
            (top.score - runner_up.score) < self._settings.margin
            if mode == "dense"
            else runner_up.score >= top.score * self._settings.sparse_ambiguity_ratio
        )
        # M10d T2: the judge fires on the true tie AND on the shaky wins
        # around it — top within judge_band of its floor, or the top-two gap
        # within judge_band above the ambiguity margin (dense scale only).
        near_band = mode == "dense" and (
            abs(top.score - threshold) <= self._learning.judge_band
            or (
                runner_up is not None
                and (top.score - runner_up.score)
                < self._settings.margin + self._learning.judge_band
            )
        )
        if ambiguous or near_band:
            judged = await self._judge(
                context, question, candidates,
                reason="ambiguous" if ambiguous else "near_band",
            )
            if judged is not None:
                self._log_decision(
                    RouteAction.DISPATCH, mode, judged.agent_key, judged.score,
                    threshold, reason="judge",
                )
                return RouteDecision(
                    action=RouteAction.DISPATCH, mode=mode,
                    agent_key=judged.agent_key, matched=judged,
                    candidates=tuple(candidates),
                )
            if ambiguous and await self._permitted(context, runner_up):
                self._log_decision(
                    RouteAction.DISAMBIGUATE, mode, top.agent_key, top.score,
                    threshold, reason="ambiguous",
                )
                return RouteDecision(
                    action=RouteAction.DISAMBIGUATE, mode=mode,
                    candidates=(top, runner_up),
                )

        self._log_decision(
            RouteAction.DISPATCH, mode, top.agent_key, top.score, threshold,
            reason="scored",
        )
        return RouteDecision(
            action=RouteAction.DISPATCH, mode=mode,
            agent_key=top.agent_key, matched=top, candidates=tuple(candidates),
        )

    async def _score(
        self, tenant_id: str, question: str, mode: str
    ) -> list[RouteCandidate]:
        params: dict = {"tenant_id": tenant_id}
        vector: list[float] | None = None
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
        candidates = [
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
        # M10d T3: fold decayed routing feedback into dense scores before
        # ranking, reusing the query embedding computed above (never embeds
        # twice). Sparse mode skips this: ts_rank is not on the cosine
        # scale the adjustment is calibrated for. Fail-soft: {} on error.
        if mode == "dense" and candidates and vector is not None:
            adjustments = await get_router_learning_service().feedback_adjustments(
                tenant_id=tenant_id, question_vector=vector
            )
            if adjustments:
                candidates = [
                    replace(c, score=c.score + adjustments.get(c.agent_key, 0.0))
                    for c in candidates
                ]
                candidates.sort(key=lambda c: c.score, reverse=True)
                logger.info(
                    "Route scores adjusted by feedback",
                    extra={
                        "adjustments": {
                            key: round(value, 4) for key, value in adjustments.items()
                        }
                    },
                )
        return candidates

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

    def _threshold_for(self, agent_key: str, mode: str) -> float:
        """M10d T2b: per-agent calibrated dispatch floor (yaml
        agents.router.thresholds_per_agent, dense cosine scale) before the
        global threshold. Sparse mode always uses the global sparse floor —
        the per-agent map is calibrated on cosine scores."""
        if mode == "dense":
            override = self._learning.thresholds_per_agent.get(agent_key)
            if override is not None:
                return float(override)
            return self._settings.threshold
        return self._settings.sparse_threshold

    def _log_decision(
        self, action: str, mode: str, agent_key: str, score: float,
        threshold: float, reason: str = "",
    ) -> None:
        """Info-level per-decision record — agent_key + score + action — so
        per-agent threshold calibration data accumulates in logs (T2b)."""
        logger.info(
            "Route decision",
            extra={
                "action": action, "mode": mode, "agent_key": agent_key,
                "score": round(float(score), 4), "threshold": threshold,
                "reason": reason,
            },
        )

    async def _judge(
        self,
        context: SessionContext,
        question: str,
        candidates: list[RouteCandidate],
        *,
        reason: str,
    ) -> RouteCandidate | None:
        """M10d T2: ONE LLM call on the ambiguous band. The judge sees only
        permitted candidates (key + display name + their registered route
        utterances) and must answer with a single token — an agent_key or
        ASK. A valid key dispatches; ASK, garbage, or any error returns
        None so the scored ladder proceeds unchanged (fail-open always)."""
        if not self._learning.judge_enabled or not candidates:
            return None
        try:
            permitted = [c for c in candidates if await self._permitted(context, c)]
            if not permitted:
                return None
            descriptions = await self._candidate_descriptions(
                context.tenant_id, permitted
            )
            lines = []
            for c in permitted:
                described = descriptions.get(c.agent_key, "")
                suffix = f" — {described}" if described else ""
                lines.append(f"{c.agent_key}: {c.display_name}{suffix}")
            prompt = self._learning.judge_prompt.replace(
                "{candidates}", "\n".join(lines)
            ).replace("{question}", question)

            from ...llm.azure_foundry import get_ace_azure_foundry

            reply = await get_ace_azure_foundry().acomplete_chat(
                messages=[{"role": "system", "content": prompt}],
                max_output_tokens=self._learning.judge_max_output_tokens,
            )
            parsed = parse_judge_reply(reply, [c.agent_key for c in permitted])
            logger.info(
                "Router judge outcome",
                extra={
                    "reason": reason,
                    "verdict": parsed or "unparseable",
                    "scores": {c.agent_key: round(c.score, 4) for c in candidates},
                },
            )
            if parsed and parsed != "ask":
                return next(c for c in permitted if c.agent_key == parsed)
        except Exception:  # noqa: BLE001 — the judge is an assist, never a gate
            logger.warning(
                "Router judge failed — falling through to scored decision",
                exc_info=True,
            )
        return None

    async def _candidate_descriptions(
        self, tenant_id: str, candidates: list[RouteCandidate]
    ) -> dict[str, str]:
        """First few registered utterances per candidate (harvest order puts
        the agent description first, then skill descriptions/examples) — the
        judge's picture of what each agent covers."""
        statement = sql_text(
            """
            SELECT utterance FROM agent_routes
            WHERE tenant_id = :tenant_id AND agent_key = :agent_key
            ORDER BY created_at
            LIMIT 3
            """
        )
        described: dict[str, str] = {}
        async with self._db.session() as session:
            for candidate in candidates:
                rows = (
                    await session.execute(
                        statement,
                        {"tenant_id": tenant_id, "agent_key": candidate.agent_key},
                    )
                ).scalars().all()
                described[candidate.agent_key] = "; ".join(str(r) for r in rows if r)
        return described

    async def _fallback(
        self,
        context: SessionContext,
        mode: str,
        candidates: list[RouteCandidate] | None = None,
        denied_candidates: RouteCandidate | None = None,
    ) -> RouteDecision:
        fallback = None
        if self._settings.fallback_agent:
            fallback = await self._candidate_for(
                context.tenant_id, self._settings.fallback_agent
            )
        if fallback is not None and await self._permitted(context, fallback):
            return RouteDecision(
                action=RouteAction.FALLBACK, mode=mode,
                agent_key=fallback.agent_key, matched=fallback,
                candidates=tuple(candidates or ()),
            )

        fallback = await self._public_candidate(context.tenant_id)
        if fallback is not None:
            return RouteDecision(
                action=RouteAction.FALLBACK, mode=mode,
                agent_key=fallback.agent_key, matched=fallback,
                candidates=tuple(candidates or ()),
            )
        return RouteDecision(
            action=RouteAction.REFUSAL_INACCESSIBLE, mode=mode,
            matched=denied_candidates,
            candidates=tuple(candidates or ()),
        )

    async def _public_candidate(self, tenant_id: str) -> RouteCandidate | None:
        statement = sql_text(
            """
            SELECT  a.agent_key, a.display_name, a.permission,
                    COALESCE(a.card_url, '') AS card_url, t.key AS team_key
            FROM registered_agents AS a JOIN odt_teams AS t ON a.team_id = t.id
            WHERE a.tenant_id = :tenant_id AND a.status = 'active'
                AND COALESCE(a.permission, '') = ''
                AND COALESCE(a.card_url, '') <> ''
            ORDER BY a.created_at, a.agent_key
            LIMIT 1
            """
        )
        try:
            async with self._db.session() as session:
                row = (await session.execute(statement, {"tenant_id": tenant_id})).mappings().first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if row is None:
            return None
        return RouteCandidate(
            agent_key=row["agent_key"],
            display_name=row["display_name"],
            team_key=row["team_key"],
            permission="",
            card_url=row["card_url"],
            score=0.0,
        )



_service: QuestionRouterService | None = None


def get_question_router_service() -> QuestionRouterService:
    global _service
    if _service is None:
        _service = QuestionRouterService()
    return _service
