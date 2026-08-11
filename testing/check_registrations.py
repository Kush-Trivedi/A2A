import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text

from backend.app.database.rdbms.pg_session import get_postgres_connector
from backend.app.scripts.init_db import initialize_database_lifecycle
from backend.app.security.authorization.enforcer import get_casbin_enforcer
from backend.app.security.session import SessionContext
from backend.app.services.agents.registry_service import get_agent_registry_service

EXPECTED = ("general", "file_qa", "policy_procedure", "econsult", "sms", "benefits", "gda")


def admin() -> SessionContext:
    now = datetime.now(timezone.utc)
    return SessionContext(
        session_id="check", tenant_id="default", user_id="admin", actor_id="admin",
        email="admin@example.org", display_name="Admin", auth_provider="entra",
        csrf_token="x", created_at=now, last_seen_at=now,
        expires_at=now + timedelta(hours=1), roles=("developer",),
    )


async def main() -> None:
    await initialize_database_lifecycle()
    await get_casbin_enforcer().initialize()
    registry = get_agent_registry_service()

    async with get_postgres_connector().session() as session:
        rows = (
            await session.execute(
                sql_text(
                    "SELECT a.agent_key, a.status, a.version, t.key AS team, "
                    "(SELECT count(*) FROM agent_routes r WHERE r.agent_key = a.agent_key) AS routes "
                    "FROM registered_agents a JOIN odt_teams t ON t.id = a.team_id "
                    "ORDER BY a.agent_key"
                )
            )
        ).all()
    for row in rows:
        print(f"{row.agent_key:18} team={row.team:15} status={row.status:10} v{row.version} routes={row.routes}")

    for key in EXPECTED:
        if key in {row.agent_key for row in rows}:
            await registry.set_agent_status(context=admin(), agent_key=key, status="active")
    print("ACTIVATED_ALL_PRESENT")


asyncio.run(main())
