import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from backend.app.database.rdbms.pg_session import get_postgres_connector
from backend.app.entity.document.document_entity import DocumentEntity
from backend.app.entity.pgvector.document_chunk_entity import DocumentChunkEntity
from backend.app.scripts.init_db import initialize_database_lifecycle
from backend.app.security.authorization.enforcer import get_casbin_enforcer
from backend.app.security.session import SessionContext
from backend.app.services.conversation.conversation_service import get_conversation_service
from backend.app.services.knowledge.source_registry_service import get_source_registry_service

POLICY_TEXT = (
    "Hand Hygiene Policy HH-101 (v3): All clinical staff must perform hand "
    "hygiene using alcohol-based hand rub for at least 20 seconds before and "
    "after every patient contact. Soap and water must be used when hands are "
    "visibly soiled. Compliance is audited monthly by the infection control team."
)


def dev() -> SessionContext:
    now = datetime.now(timezone.utc)
    return SessionContext(
        session_id="consult-e2e", tenant_id="default", user_id="dev", actor_id="dev",
        email="dev@example.org", display_name="Dev", auth_provider="entra",
        csrf_token="x", created_at=now, last_seen_at=now,
        expires_at=now + timedelta(hours=1), roles=("developer",),
    )


async def main() -> None:
    await initialize_database_lifecycle()
    await get_casbin_enforcer().initialize()

    # Clinical Care loads ONE policy doc into their source and binds it to
    # the policy_procedure agent (three-way map recorded).
    await get_source_registry_service().register_source(
        context=dev(),
        source_name="sharepoint:policies",
        owner_team_key="clinical_care",
        description="Policy library (seeded for consult E2E)",
        agents=["policy_procedure"],
        roles=["developer", "nurse"],
    )
    # Plant the doc directly (sparse-mode path: embeddings not configured yet;
    # real ingestion embeds once Foundry creds land).
    async with get_postgres_connector().session() as session:
        stale = (
            await session.exec(
                select(DocumentEntity).where(
                    DocumentEntity.tenant_id == "default",
                    DocumentEntity.source_name == "sharepoint:policies",
                )
            )
        ).all()
        for document in stale:
            await session.delete(document)  # chunks cascade
        document_id = uuid.uuid4().hex
        session.add(
            DocumentEntity(
                id=document_id, tenant_id="default", actor_id="dev",
                source_type="sharepoint", source_name="sharepoint:policies",
                status="processed", chunk_count=1,
            )
        )
        session.add(
            DocumentChunkEntity(
                id=uuid.uuid4().hex, tenant_id="default",
                document_id=document_id, chunk_index=0,
                content=POLICY_TEXT, embedding_text=POLICY_TEXT,
                metadata_json={"knowledge_source": "sharepoint:policies"},
            )
        )
    print("policy doc planted into sharepoint:policies (sparse-retrievable)")

    chat = get_conversation_service()

    print("--- econsult asked a POLICY question (its own sources are empty) ---")
    turn = await chat.send(
        context=dev(), agent_id="econsult",
        message="What is the hand hygiene policy?",
    )
    print(f"  answered by: {turn.agent_id}")
    print(f"  answer: {turn.answer[:420]}")

    print("--- agent-binding guard: econsult may NOT read the policy source directly ---")
    turn2 = await chat.send(
        context=dev(), agent_id="policy_procedure",
        message="What is the hand hygiene policy?",
    )
    print(f"  policy_procedure direct: {turn2.answer[:200]}")

    print("CONSULT_E2E_DONE")


asyncio.run(main())
