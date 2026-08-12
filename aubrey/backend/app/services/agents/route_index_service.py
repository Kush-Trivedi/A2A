"""The router's index, rebuilt at registration.

Utterances are harvested from what the TEAM declares — the agent's
description plus each skill's description and examples. No intent lists in
code; add an agent and it becomes routable, change its skills and its
routing changes. In dense mode each utterance is embedded; sparse mode
(local, credential-less) relies on the generated tsvector alone.

The collision gate warns (never blocks) when a new agent's utterances sit
too close to another team's — surfaced in the registration response."""

import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import delete, text as sql_text

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import AgentRouteEntity, RegisteredAgentEntity
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, ValidationError

logger = Logger(__name__).get_logger()

ROUTER_MODES = ("dense", "sparse")


@dataclass(frozen=True)
class RouterSettings:
    mode: str
    threshold: float
    sparse_threshold: float
    margin: float
    sparse_ambiguity_ratio: float
    switch_margin: float
    overlap_threshold: float
    fallback_agent: str
    utterance_cap: int


@lru_cache(maxsize=1)
def get_router_settings() -> RouterSettings:
    router = get_application_context().agents["router"]
    settings = RouterSettings(
        mode=str(router["mode"]).strip().lower(),
        threshold=float(router["threshold"]),
        sparse_threshold=float(router["sparse_threshold"]),
        margin=float(router["margin"]),
        sparse_ambiguity_ratio=float(router["sparse_ambiguity_ratio"]),
        switch_margin=float(router["switch_margin"]),
        overlap_threshold=float(router["overlap_threshold"]),
        fallback_agent=str(router["fallback_agent"]).strip().lower(),
        utterance_cap=int(router["utterance_cap"]),
    )
    if settings.mode not in ROUTER_MODES:
        raise ValidationError(
            f"agents.router.mode '{settings.mode}' is unknown. "
            f"Valid modes: {', '.join(ROUTER_MODES)}."
        )
    return settings


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def harvest_utterances(agent: RegisteredAgentEntity, *, cap: int) -> list[str]:
    utterances: list[str] = []
    if agent.description.strip():
        utterances.append(agent.description.strip())
    for skill in agent.skills or []:
        description = str(skill.get("description") or "").strip()
        if description:
            utterances.append(description)
        for example in skill.get("examples") or []:
            example = str(example).strip()
            if example:
                utterances.append(example)
    return list(dict.fromkeys(utterances))[:cap]


class RouteIndexService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()
        self._settings = get_router_settings()

    async def rebuild_for_agent(
        self, *, tenant_id: str, agent: RegisteredAgentEntity
    ) -> tuple[int, list[dict]]:
        """Full replace of the agent's route rows. Returns (utterance count,
        overlap warnings against OTHER agents)."""
        utterances = harvest_utterances(agent, cap=self._settings.utterance_cap)

        embeddings: list[list[float] | None] = [None] * len(utterances)
        if self._settings.mode == "dense" and utterances:
            from ..knowledge import get_embedding_service

            embeddings = list(await get_embedding_service().embed(utterances))

        try:
            async with self._db.session() as session:
                await session.execute(
                    delete(AgentRouteEntity).where(
                        AgentRouteEntity.tenant_id == tenant_id,
                        AgentRouteEntity.agent_key == agent.agent_key,
                    )
                )
                for utterance, embedding in zip(utterances, embeddings):
                    session.add(
                        AgentRouteEntity(
                            id=uuid.uuid4().hex,
                            tenant_id=tenant_id,
                            agent_key=agent.agent_key,
                            utterance=utterance,
                            embedding=embedding,
                        )
                    )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        overlaps: list[dict] = []
        if self._settings.mode == "dense":
            overlaps = await self._find_overlaps(tenant_id, agent.agent_key, utterances, embeddings)
        logger.info(
            "Route index rebuilt",
            extra={
                "agent_key": agent.agent_key,
                "utterances": len(utterances),
                "overlaps": len(overlaps),
            },
        )
        return len(utterances), overlaps

    async def _find_overlaps(
        self,
        tenant_id: str,
        agent_key: str,
        utterances: list[str],
        embeddings: list[list[float] | None],
    ) -> list[dict]:
        """Nearest neighbour among OTHER agents' utterances — a warning when
        two teams claim near-identical ground."""
        overlaps: list[dict] = []
        statement = sql_text(
            """
            SELECT r.agent_key, r.utterance,
                   1 - (r.embedding <=> CAST(:vec AS vector)) AS score
            FROM agent_routes r
            WHERE r.tenant_id = :tenant_id
              AND r.agent_key != :agent_key
              AND r.embedding IS NOT NULL
            ORDER BY r.embedding <=> CAST(:vec AS vector)
            LIMIT 1
            """
        )
        async with self._db.session() as session:
            for utterance, embedding in zip(utterances, embeddings):
                if embedding is None:
                    continue
                row = (
                    await session.execute(
                        statement,
                        {
                            "tenant_id": tenant_id,
                            "agent_key": agent_key,
                            "vec": _vector_literal(embedding),
                        },
                    )
                ).mappings().first()
                if row and float(row["score"] or 0.0) >= self._settings.overlap_threshold:
                    overlaps.append(
                        {
                            "agent_key": row["agent_key"],
                            "utterance": utterance,
                            "score": round(float(row["score"]), 4),
                        }
                    )
        return overlaps


_service: RouteIndexService | None = None


def get_route_index_service() -> RouteIndexService:
    global _service
    if _service is None:
        _service = RouteIndexService()
    return _service
