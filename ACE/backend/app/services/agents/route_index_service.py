import uuid

from sqlalchemy import text as sql_text
from sqlmodel import delete, select

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import AgentRouteEntity
from ...services.embedding.embedding_service import EmbeddingService, get_embedding_service
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError

logger = Logger(__name__).get_logger()


class RouteIndexService:
    """Builds and maintains the question-router index at registration time.

    Routing updates itself the moment an agent registers — no ACE change, no
    router retraining. When embeddings are configured the utterances are
    embedded for dense routing AND checked for cross-agent overlap (the
    collision gate); without embeddings, utterances are stored text-only and
    the sparse FTS mode covers routing.
    """

    _MAX_UTTERANCES = 32

    def __init__(self, embedding: EmbeddingService | None = None) -> None:
        self._connector = get_postgres_connector()
        self._embedding = embedding or get_embedding_service()

    @staticmethod
    def _router_settings() -> dict:
        return get_application_context().agents.get("router", {}) or {}

    @classmethod
    def _utterances(
        cls, *, display_name: str, description: str, skills: list[dict]
    ) -> list[str]:
        collected: list[str] = []
        if description.strip():
            collected.append(description.strip())
        for skill in skills or []:
            skill_description = str(skill.get("description", "") or "").strip()
            if skill_description:
                collected.append(skill_description)
            for example in skill.get("examples") or []:
                example_text = str(example).strip()
                if example_text:
                    collected.append(example_text)
        deduped = list(dict.fromkeys(collected))
        if not deduped and display_name.strip():
            deduped = [display_name.strip()]
        return deduped[: cls._MAX_UTTERANCES]

    async def rebuild_for_agent(
        self,
        *,
        tenant_id: str,
        agent_key: str,
        display_name: str,
        description: str,
        skills: list[dict],
    ) -> list[dict]:
        """Replace the agent's route rows; returns cross-agent overlap
        warnings: [{agent_key, utterance, score}] above the overlap threshold."""
        utterances = self._utterances(
            display_name=display_name, description=description, skills=skills
        )

        vectors: list[list[float] | None] = []
        embeddings_available = True
        for utterance in utterances:
            if not embeddings_available:
                vectors.append(None)
                continue
            try:
                vectors.append(await self._embedding.embed_query(utterance))
            except Exception:  # noqa: BLE001 — sparse mode covers routing without embeddings
                embeddings_available = False
                vectors.append(None)
        if not embeddings_available:
            logger.info(
                "Route index built text-only (embeddings not configured); "
                "sparse routing active",
                extra={"agent_key": agent_key},
            )

        try:
            async with self._connector.session() as session:
                await session.exec(
                    delete(AgentRouteEntity).where(
                        AgentRouteEntity.tenant_id == tenant_id,
                        AgentRouteEntity.agent_key == agent_key,
                    )
                )
                for utterance, vector in zip(utterances, vectors):
                    session.add(
                        AgentRouteEntity(
                            id=uuid.uuid4().hex,
                            tenant_id=tenant_id,
                            agent_key=agent_key,
                            utterance=utterance,
                            embedding=vector,
                        )
                    )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        if not embeddings_available:
            return []
        return await self._find_overlaps(
            tenant_id=tenant_id,
            agent_key=agent_key,
            utterances=utterances,
            vectors=vectors,
        )

    async def _find_overlaps(
        self,
        *,
        tenant_id: str,
        agent_key: str,
        utterances: list[str],
        vectors: list[list[float] | None],
    ) -> list[dict]:
        threshold = float(self._router_settings().get("overlap_threshold") or 0.86)
        overlaps: list[dict] = []
        try:
            async with self._connector.session() as session:
                for utterance, vector in zip(utterances, vectors):
                    if not vector:
                        continue
                    vector_literal = "[" + ",".join(str(float(x)) for x in vector) + "]"
                    row = (
                        await session.execute(
                            sql_text(
                                "SELECT agent_key, 1 - (embedding <=> CAST(:vec AS vector)) AS similarity "
                                "FROM agent_routes "
                                "WHERE tenant_id = :tenant AND agent_key != :agent "
                                "AND embedding IS NOT NULL "
                                "ORDER BY embedding <=> CAST(:vec AS vector) LIMIT 1"
                            ),
                            {"vec": vector_literal, "tenant": tenant_id, "agent": agent_key},
                        )
                    ).first()
                    if row is not None and float(row.similarity) >= threshold:
                        overlaps.append(
                            {
                                "agent_key": str(row.agent_key),
                                "utterance": utterance,
                                "score": round(float(row.similarity), 4),
                            }
                        )
        except Exception:  # noqa: BLE001 — overlap check is advisory, never blocks registration
            logger.warning("Route overlap check failed; skipping", exc_info=True)
            return []
        if overlaps:
            logger.warning(
                "Route overlap detected — sharpen skill examples or confirm intent",
                extra={"agent_key": agent_key, "overlaps": overlaps},
            )
        return overlaps

    async def remove_agent(self, *, tenant_id: str, agent_key: str) -> None:
        try:
            async with self._connector.session() as session:
                await session.exec(
                    delete(AgentRouteEntity).where(
                        AgentRouteEntity.tenant_id == tenant_id,
                        AgentRouteEntity.agent_key == agent_key,
                    )
                )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list_for_agent(
        self, *, tenant_id: str, agent_key: str
    ) -> list[AgentRouteEntity]:
        try:
            async with self._connector.session() as session:
                rows = (
                    await session.exec(
                        select(AgentRouteEntity).where(
                            AgentRouteEntity.tenant_id == tenant_id,
                            AgentRouteEntity.agent_key == agent_key,
                        )
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_service: RouteIndexService | None = None


def get_route_index_service() -> RouteIndexService:
    global _service
    if _service is None:
        _service = RouteIndexService()
    return _service
