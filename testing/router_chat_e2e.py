import asyncio
from datetime import datetime, timedelta, timezone

from backend.app.scripts.init_db import initialize_database_lifecycle
from backend.app.security.authorization.enforcer import get_casbin_enforcer
from backend.app.security.session import SessionContext
from backend.app.services.agents.question_router_service import get_question_router_service
from backend.app.services.conversation.conversation_service import get_conversation_service


def context_for(roles: tuple[str, ...], user: str) -> SessionContext:
    now = datetime.now(timezone.utc)
    return SessionContext(
        session_id=f"e2e-{user}", tenant_id="default", user_id=user, actor_id=user,
        email=f"{user}@example.org", display_name=user.title(), auth_provider="entra",
        csrf_token="x", created_at=now, last_seen_at=now,
        expires_at=now + timedelta(hours=1), roles=roles,
    )


async def main() -> None:
    await initialize_database_lifecycle()
    await get_casbin_enforcer().initialize()
    router = get_question_router_service()
    dev = context_for(("developer",), "dev")

    print("--- ROUTER (sparse mode, no LLM creds) ---")
    for question in (
        "What is the hand hygiene policy for nurses?",
        "When is open enrollment for benefits?",
        "How do I submit an eConsult referral?",
        "How many patients were admitted last month?",
        "zzz qqq xyzzy nonsense",
    ):
        decision = await router.route(context=dev, question=question)
        print(
            f"  {question[:44]:46} -> {decision.action.value:22} "
            f"agent={decision.agent_key or decision.matched_agent} mode={decision.mode}"
        )

    print("--- ROUTER: inaccessible match (sms_patient asks benefits) ---")
    patient = context_for(("sms_patient",), "patient")
    decision = await router.route(
        context=patient, question="When is open enrollment for benefits?"
    )
    print(f"  action={decision.action.value} matched={decision.matched_agent}")

    print("--- FULL CHAT TURN (auto-routed, real A2A round trip) ---")
    chat = get_conversation_service()
    result = await chat.send(context=dev, agent_id=None, message="What can I access?")
    print(f"  answered by: {result.agent_id}")
    print(f"  answer: {result.answer[:220]}")

    print("--- FULL CHAT TURN (explicit agent, benefits) ---")
    result2 = await chat.send(context=dev, agent_id="benefits", message="When is open enrollment?")
    print(f"  answered by: {result2.agent_id}")
    print(f"  answer: {result2.answer[:160]}")

    print("E2E_DONE")


asyncio.run(main())
