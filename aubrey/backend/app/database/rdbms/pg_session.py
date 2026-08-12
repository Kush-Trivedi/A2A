from functools import lru_cache
from typing import AsyncGenerator
from ...utils.common.logger import Logger
from .pg_connector import PostgresConnector
from sqlmodel.ext.asyncio.session import AsyncSession


logger = Logger(__name__).get_logger()

@lru_cache(maxsize=1)
def get_postgres_connector() -> PostgresConnector:
    return PostgresConnector()

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    connector = get_postgres_connector()
    try:
        async with connector.get_async_session() as session:
            yield session
    except Exception as e:
        logger.error(
            "Error in async session generator",
            extra={"error_code": "async_session_generation_failed"},
            exc_info=True
        )
        raise

async def dispose_postgres() -> None:
    if get_postgres_connector.cache_info().currsize:
        await get_postgres_connector().dispose()