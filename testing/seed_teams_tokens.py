import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from backend.app.scripts.init_db import initialize_database_lifecycle
from backend.app.security.authorization.enforcer import get_casbin_enforcer
from backend.app.security.session import SessionContext
from backend.app.services.agents.registry_service import get_agent_registry_service
from backend.app.services.agents.team_token_service import get_team_token_service

TEAMS = {
    "ace_platform": "ACE Platform",
    "clinical_care": "Clinical Care",
    "hr_benefits": "HR Benefits",
    "data_analytics": "Data Analytics",
}


def admin() -> SessionContext:
    now = datetime.now(timezone.utc)
    return SessionContext(
        session_id="seed", tenant_id="default", user_id="admin", actor_id="admin",
        email="admin@example.org", display_name="Admin", auth_provider="entra",
        csrf_token="x", created_at=now, last_seen_at=now,
        expires_at=now + timedelta(hours=1), roles=("developer",),
    )


async def main() -> None:
    await initialize_database_lifecycle()
    await get_casbin_enforcer().initialize()
    registry = get_agent_registry_service()
    tokens = get_team_token_service()

    issued: dict[str, str] = {}
    for key, name in TEAMS.items():
        await registry.register_team(
            context=admin(), key=key, name=name,
            description=f"{name} team", contact_email=f"{key}@example.org",
        )
        issued[key] = await tokens.issue(context=admin(), team_key=key, label="local-smoke")
        print(f"team {key}: registered, token issued")

    with open(sys.argv[1], "w", encoding="utf-8") as file:
        json.dump(issued, file)
    print("TOKENS_WRITTEN")


asyncio.run(main())
