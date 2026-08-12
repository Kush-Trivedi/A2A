from sqlmodel import SQLModel

from ..database.rdbms.pg_session import get_postgres_connector
from ..entity import agents as _agent_entities  # noqa: F401 — registers tables
from ..entity import authz as _authz_entities  # noqa: F401 — registers tables
from ..entity import documents as _document_entities  # noqa: F401 — registers tables

# Schema comes entirely from the entity classes: create_all makes every
# missing table at startup and never alters existing ones. A dev database
# from older code is reset (drop + restart); production schema evolution is
# a migration tool's job when it lands.


async def initialize_database_lifecycle() -> None:
    connector = get_postgres_connector()
    async with connector.connect() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
