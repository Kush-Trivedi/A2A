from sqlmodel import SQLModel

from ..database.rdbms.pg_session import get_postgres_connector
from ..entity import agents as _agent_entities  # noqa: F401 — registers tables
from ..entity import authz as _authz_entities  # noqa: F401 — registers tables
from ..entity import chat as _chat_entities  # noqa: F401 — registers tables
from ..entity import data as _data_entities  # noqa: F401 — registers tables
from ..entity import documents as _document_entities  # noqa: F401 — registers tables
from ..entity import knowledge as _knowledge_entities  # noqa: F401 — registers tables
from ..entity import memory as _memory_entities  # noqa: F401 — registers tables
from ..entity import sms as _sms_entities  # noqa: F401 — registers tables
from ..entity.knowledge import ensure_pgvector_extension, ensure_pgvector_indexes
from ..entity.memory import ensure_memory_indexes
from ..utils.errors import DatabaseError

# Schema comes entirely from the entity classes: create_all makes every
# missing table at startup and never alters existing ones. A dev database
# from older code is reset (drop + restart); production schema evolution is
# a migration tool's job when it lands.
#
# Order matters: the pgvector extension must exist before create_all makes
# the vector(3072) columns, and the HNSW indexes need the tables.


async def initialize_database_lifecycle() -> None:
    connector = get_postgres_connector()
    async with connector.connect() as connection:
        try:
            await ensure_pgvector_extension(connection)
        except Exception as exc:
            raise DatabaseError(
                "The pgvector extension could not be enabled. Install pgvector "
                "for this Postgres server (postgresql-17-pgvector) and grant "
                "CREATE EXTENSION, or create it once as a superuser: "
                "CREATE EXTENSION vector;",
                cause=exc,
            ) from exc
        await connection.run_sync(SQLModel.metadata.create_all)
        await ensure_pgvector_indexes(connection)
        await ensure_memory_indexes(connection)
