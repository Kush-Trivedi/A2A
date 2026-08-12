"""Which agents can THIS user use — live registry + Casbin, computed fresh
per call. The general agent injects this as grounded context, so 'what can
I access?' answers are always current data, never a maintained list."""

from dataclasses import dataclass

from sqlalchemy import text as sql_text

from ...database.rdbms.pg_session import get_postgres_connector
from ...security.authorization.enforcer import get_casbin_enforcer
from ...utils.errors import DatabaseError


@dataclass(frozen=True)
class CatalogAgent:
    agent_key: str
    display_name: str
    description: str
    team_key: str


class AgentCatalogService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def list_for(
        self, *, tenant_id: str, roles: tuple[str, ...]
    ) -> list[CatalogAgent]:
        statement = sql_text(
            """
            SELECT a.agent_key, a.display_name, a.description, a.permission,
                   t.key AS team_key
            FROM registered_agents a JOIN odt_teams t ON t.id = a.team_id
            WHERE a.tenant_id = :tenant_id AND a.status = 'active'
            ORDER BY a.agent_key
            """
        )
        try:
            async with self._db.session() as session:
                rows = (
                    await session.execute(statement, {"tenant_id": tenant_id})
                ).mappings().all()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        enforcer = get_casbin_enforcer()
        catalog: list[CatalogAgent] = []
        for row in rows:
            permission = row["permission"] or ""
            if permission:
                if not roles:
                    continue
                allowed = await enforcer.enforce_any_role(
                    roles, tenant_id, f"agent:{row['agent_key']}", permission
                )
                if not allowed:
                    continue
            catalog.append(
                CatalogAgent(
                    agent_key=row["agent_key"],
                    display_name=row["display_name"],
                    description=row["description"] or "",
                    team_key=row["team_key"],
                )
            )
        return catalog


_service: AgentCatalogService | None = None


def get_agent_catalog_service() -> AgentCatalogService:
    global _service
    if _service is None:
        _service = AgentCatalogService()
    return _service
