import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.knowledge import KnowledgeSourceEntity
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, ValidationError

logger = Logger(__name__).get_logger()


class SourceRegistryService:
    """Records the three-way access map for every ingested knowledge source:
    owner team -> bound agents -> reader roles.

    Registration seeds Casbin `knowledge:<source> read` for the reader roles
    (same discipline as agent registration). The agent binding is enforced by
    the retrieve capability via `filter_sources_for_agent` — an agent can
    never read another team's source by guessing its name. Sources without a
    registry row (legacy / session uploads) fall back to role-only
    enforcement, so nothing existing breaks.
    """

    def __init__(self, enforcer: CasbinEnforcer | None = None) -> None:
        self._connector = get_postgres_connector()
        self._enforcer = enforcer or get_casbin_enforcer()

    async def register_source(
        self,
        *,
        context: SessionContext,
        source_name: str,
        owner_team_key: str,
        connection_name: str = "",
        description: str = "",
        location: dict | None = None,
        chunking: dict | None = None,
        embedding: dict | None = None,
        agents: list[str] | None = None,
        roles: list[str] | None = None,
    ) -> tuple[KnowledgeSourceEntity, int]:
        """Upsert by (tenant, source_name); only the owning team may update.
        Returns (source, policies_seeded)."""
        normalized = (source_name or "").strip().lower()
        if not normalized:
            raise ValidationError("source_name is required.")
        owner = (owner_team_key or "").strip().lower()
        if not owner:
            raise ValidationError("owner_team_key is required.")

        try:
            async with self._connector.session() as session:
                existing = (
                    await session.exec(
                        select(KnowledgeSourceEntity).where(
                            KnowledgeSourceEntity.tenant_id == context.tenant_id,
                            KnowledgeSourceEntity.source_name == normalized,
                        )
                    )
                ).first()
                if existing is not None:
                    if existing.owner_team_key != owner:
                        raise ValidationError(
                            "Knowledge source is owned by another team.",
                            details={
                                "source_name": normalized,
                                "owner_team": existing.owner_team_key,
                            },
                        )
                    existing.connection_name = connection_name
                    existing.description = description
                    existing.location = dict(location or {})
                    existing.chunking = dict(chunking or {})
                    existing.embedding = dict(embedding or {})
                    existing.agents = sorted({a.strip().lower() for a in (agents or []) if a.strip()})
                    existing.roles = sorted({r.strip() for r in (roles or []) if r.strip()})
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                    source = existing
                else:
                    source = KnowledgeSourceEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=context.tenant_id,
                        source_name=normalized,
                        owner_team_key=owner,
                        connection_name=connection_name,
                        description=description,
                        location=dict(location or {}),
                        chunking=dict(chunking or {}),
                        embedding=dict(embedding or {}),
                        agents=sorted({a.strip().lower() for a in (agents or []) if a.strip()}),
                        roles=sorted({r.strip() for r in (roles or []) if r.strip()}),
                    )
                    session.add(source)
        except ValidationError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        seeded = await self._seed_read_policies(
            tenant_id=context.tenant_id,
            source_name=normalized,
            roles=list(source.roles or []),
        )
        logger.info(
            "Knowledge source registered",
            extra={
                "source_name": normalized,
                "owner_team": owner,
                "agents": list(source.agents or []),
                "policies_seeded": seeded,
            },
        )
        return source, seeded

    async def _seed_read_policies(
        self, *, tenant_id: str, source_name: str, roles: list[str]
    ) -> int:
        if not self._enforcer.enabled or not roles:
            return 0
        seeded = 0
        for role in roles:
            added = await self._enforcer.add_policy(
                role, tenant_id, f"knowledge:{source_name}", "read"
            )
            seeded += int(bool(added))
        return seeded

    async def list_sources(
        self, *, context: SessionContext, team_key: str | None = None
    ) -> list[KnowledgeSourceEntity]:
        try:
            async with self._connector.session() as session:
                statement = select(KnowledgeSourceEntity).where(
                    KnowledgeSourceEntity.tenant_id == context.tenant_id
                )
                if team_key:
                    statement = statement.where(
                        KnowledgeSourceEntity.owner_team_key == team_key.strip().lower()
                    )
                rows = (
                    await session.exec(statement.order_by(KnowledgeSourceEntity.source_name))
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def filter_sources_for_agent(
        self, *, tenant_id: str, agent_key: str, requested_sources: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Drop registered sources the agent is NOT bound to. Sources without
        a registry row pass through (legacy/role-only enforcement)."""
        if not requested_sources or not agent_key.strip():
            return requested_sources
        agent = agent_key.strip().lower()
        try:
            async with self._connector.session() as session:
                rows = (
                    await session.exec(
                        select(KnowledgeSourceEntity).where(
                            KnowledgeSourceEntity.tenant_id == tenant_id,
                            KnowledgeSourceEntity.source_name.in_(  # type: ignore[attr-defined]
                                [s.strip().lower() for s in requested_sources]
                            ),
                        )
                    )
                ).all()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        registered = {row.source_name: row for row in rows}
        allowed: list[str] = []
        for source in requested_sources:
            row = registered.get(source.strip().lower())
            if row is None or agent in (row.agents or []):
                allowed.append(source)
            else:
                logger.warning(
                    "Agent is not bound to knowledge source — dropped from retrieval",
                    extra={"agent_key": agent, "source_name": source},
                )
        return tuple(allowed)


_service: SourceRegistryService | None = None


def get_source_registry_service() -> SourceRegistryService:
    global _service
    if _service is None:
        _service = SourceRegistryService()
    return _service
