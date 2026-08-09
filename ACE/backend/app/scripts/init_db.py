from sqlalchemy import text
from sqlmodel import SQLModel
from ..entity import agents as _agent_entities # noqa: F401
from ..entity import authz as  _authz_entites # noqa: F401
from ..entity import chat as _chat_entities # noqa: F401
from ..entity import document as _document_entities # noqa: F401
from ..entity import pgvector as _pgvector_entities # noqa: F401
from ..entity import sms as _sms_entities # noqa: F401
from ..entity import teams as _teams_entities # noqa: F401
from ..database.rdbms.pg_session import get_postgres_connector
from ..entity.pgvector.schema import ensure_pgvector_indexes

# Idempotent column additions for tables that predate newer entity fields.
# create_all never ALTERs existing tables; these keep older databases in sync.
_SCHEMA_UPGRADE_SQL: tuple[str, ...] = (
    "ALTER TABLE registered_agents ADD COLUMN IF NOT EXISTS skills jsonb NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE registered_agents ADD COLUMN IF NOT EXISTS card_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE registered_agents ADD COLUMN IF NOT EXISTS prompts jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS actor_id text",
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()",
)


async def initialize_database_lifecycle() -> None:
    postgres_connector = get_postgres_connector()
    async with postgres_connector.connect() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
        for statement in _SCHEMA_UPGRADE_SQL:
            await connection.execute(text(statement))
        await ensure_pgvector_indexes(connection)