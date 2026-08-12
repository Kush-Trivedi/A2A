import contextlib
from sqlalchemy import event
from typing import Any, Optional
from sqlalchemy.engine import URL
from ...utils.common.logger import Logger
from collections.abc import AsyncGenerator
from sqlmodel.ext.asyncio.session import AsyncSession
from ...utils.azure.azure_helpers import AzurePostgresToken

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from .pg_settings import DatabaseSettings, get_database_settings

logger = Logger(__name__).get_logger()

_VALID_SSL_MODES = {"disable", "allow", "prefer", "require"}


class PostgresConnector:
    _async_engine: Optional[AsyncEngine]
    _async_sessionmaker: Optional[async_sessionmaker[AsyncSession]]
    _azure_token: Optional[AzurePostgresToken]

    def __init__(self) -> None:
        self._async_engine = None
        self._async_sessionmaker = None
        self._azure_token = None

        ds = get_database_settings()

        if not ds.host:
            raise ValueError("Database host is required.")

        if ds.ssl_mode not in _VALID_SSL_MODES:
            raise ValueError(
                "ACE_DB_SSL_MODE must be one of: disable, allow, prefer, require."
            )

        connect_args: dict[str, Any] = {"timeout": ds.timeout}
        if ds.ssl_mode == "require":
            connect_args["ssl"] = True

        if ds.auth_mode == "password":
            self._configure_password_engine(ds, connect_args)
        elif ds.auth_mode == "managed_identity":
            self._configure_managed_identity_engine(ds, connect_args)
        else:
            raise ValueError("ACE_DB_AUTH_MODE must be 'password' or 'managed_identity'.")

        self._async_sessionmaker = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            class_=AsyncSession,
            autoflush=False,
        )

    def _configure_password_engine(self, ds: DatabaseSettings, connect_args: dict) -> None:
        if not ds.password:
            raise ValueError("Database password is required when auth_mode=password.")

        logger.info("[blue]PostgreSQL: password async connection")
        url = URL.create(
            drivername="postgresql+asyncpg",
            username=ds.user,
            password=ds.password,
            host=ds.host,
            port=ds.port,
            database=ds.dbname,
        )

        self._async_engine = create_async_engine(
            url=url,
            echo=False,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_recycle=ds.pool_recycle,
            pool_size=ds.pool_size,
            max_overflow=ds.max_overflow,
        )

    def _configure_managed_identity_engine(
        self, ds: DatabaseSettings, connect_args: dict
    ) -> None:
        logger.info("[blue]PostgreSQL: managed-identity token async connection")
        url = URL.create(
            drivername="postgresql+asyncpg",
            username=ds.user,
            host=ds.host,
            port=ds.port,
            database=ds.dbname,
        )

        from ...config.application_context import get_application_context

        self._azure_token = AzurePostgresToken(
            managed_identity_client_id=get_application_context().managed_identity_client_id or None
        )
        self._async_engine = create_async_engine(
            url=url,
            echo=False,
            connect_args=connect_args,
            pool_recycle=min(ds.pool_recycle, 800),
            pool_pre_ping=True,
            pool_size=ds.pool_size,
            max_overflow=ds.max_overflow,
        )

        @event.listens_for(self._async_engine.sync_engine, "do_connect")
        def inject_azure_token(dialect, conn_rec, cargs, cparams):
            try:
                cparams["password"] = self._azure_token.generate_token()
                logger.debug("Azure PostgreSQL token refreshed successfully")
            except Exception:
                logger.error(
                    "Failed to refresh Azure PostgreSQL token",
                    extra={"error_code": "azure_postgres_token_refresh_failed"},
                    exc_info=True,
                )
                raise

    @property
    def engine(self) -> AsyncEngine:
        if self._async_engine is None:
            raise RuntimeError("Async engine is not initialized")
        return self._async_engine

    async def dispose(self) -> None:
        if self._async_engine is not None:
            await self._async_engine.dispose()
            self._async_engine = None
            self._async_sessionmaker = None
            logger.info("[blue]PostgreSQL async engine disposed")

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncGenerator[AsyncConnection, None]:
        if self._async_engine is None:
            raise RuntimeError("Async engine is not initialized")
        try:
            async with self._async_engine.begin() as connection:
                logger.debug("Async database connection opened")
                yield connection
        except Exception:
            logger.error(
                "Async database connection failed",
                extra={"error_code": "async_connection_failed"},
                exc_info=True,
            )
            raise

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._async_sessionmaker is None:
            raise RuntimeError("Async sessionmaker is not initialized")
        async_session: AsyncSession = self._async_sessionmaker()
        try:
            logger.debug("Async database session opened")
            yield async_session
            await async_session.commit()
        except Exception:
            logger.error(
                "Async database session failed",
                extra={"error_code": "async_session_failed"},
                exc_info=True,
            )
            try:
                await async_session.rollback()
            except Exception:
                logger.error(
                    "Async database rollback failed",
                    extra={"error_code": "async_rollback_failed"},
                    exc_info=True,
                )
            raise
        finally:
            try:
                await async_session.close()
                logger.debug("Async database session closed")
            except Exception:
                logger.error(
                    "Async database session close failed",
                    extra={"error_code": "async_session_close_failed"},
                    exc_info=True,
                )
