from sqlmodel import SQLModel
from ..entity import agents as _agent_entities # noqa: F401
from ..entity import authz as  _authz_entites # noqa: F401
from ..entity import chat as _chat_entities # noqa: F401
from ..entity import connections as _connection_entities # noqa: F401
from ..entity import knowledge as _knowledge_entities # noqa: F401
from ..entity import document as _document_entities # noqa: F401
from ..entity import pgvector as _pgvector_entities # noqa: F401
from ..entity import sms as _sms_entities # noqa: F401
from ..entity import teams as _teams_entities # noqa: F401
from ..database.rdbms.pg_session import get_postgres_connector
from ..entity.pgvector.schema import ensure_pgvector_indexes

# Schema comes ENTIRELY from the entity classes: create_all makes every
# missing table at startup. create_all never ALTERs an existing table — a
# dev database from older code is reset (drop + restart), and production
# schema evolution is Alembic's job when it lands (see STATE.md backlog).


async def initialize_database_lifecycle() -> None:
    postgres_connector = get_postgres_connector()
    async with postgres_connector.connect() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
        await ensure_pgvector_indexes(connection)
