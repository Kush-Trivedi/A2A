from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import health_check_router, register_exception_handlers, v1_router
from .config.application_context import get_application_context
from .config.settings_validator import get_settings_validator
from .database.rdbms.pg_session import dispose_postgres
from .scripts.init_db import initialize_database_lifecycle
from .security import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    get_auth_settings,
)
from .security.authorization import get_casbin_enforcer
from .utils.common.log_redaction import install_log_redaction
from .utils.common.logger import Logger

logger = Logger(__name__).get_logger()


install_log_redaction()

class ApplicationFactory:
    def __init__(self) -> None:
        self._context = get_application_context()
        self._settings = get_auth_settings()
        self._enforcer = get_casbin_enforcer()

    @property
    def _is_local(self) -> bool:
        return self._context.environment == "local"


    def _lifespan(self):
        @asynccontextmanager
        async def lifespan(_app: FastAPI):
            logger.info("Application startup: validating configuration")
            install_log_redaction()
            get_settings_validator().validate_and_log()
            logger.info("Application startup: initializing database + authz")
            await initialize_database_lifecycle()
            await self._enforcer.initialize()
            try:
                yield
            finally:
                logger.info("Application shutdown: releasing resources")
                await self._enforcer.close()
                await dispose_postgres()

        return lifespan


    def _apply_middleware(self, app: FastAPI) -> None:
        app.add_middleware(SecurityHeadersMiddleware, settings=self._settings)
        app.add_middleware(RequestContextMiddleware)
        app.add_middleware(GZipMiddleware, minimum_size=self._settings.gzip_min_bytes)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(self._settings.allowed_origins),
            allow_credentials=self._settings.cors_allow_credentials,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        )
        if self._settings.allowed_hosts:
            app.add_middleware(
                TrustedHostMiddleware,
                allowed_hosts=list(self._settings.allowed_hosts),
            )
        app.add_middleware(
            BodySizeLimitMiddleware, max_bytes=self._settings.max_request_bytes
        )

    def create_app(self) -> FastAPI:
        docs_enabled = self._is_local
        app = FastAPI(
            title="Aubrey API",
            description="Aubrey backend — authentication, authorization, and agent services.",
            version="1.0.0",
            swagger_ui_parameters={"defaultModelsExpandDepth": -1},
            docs_url="/docs" if docs_enabled else None,
            redoc_url="/redoc" if docs_enabled else None,
            openapi_url="/openapi.json" if docs_enabled else None,
            lifespan=self._lifespan(),
        )

        self._apply_middleware(app)
        register_exception_handlers(app)

        app.include_router(health_check_router)
        app.include_router(v1_router)

        return app


def create_app() -> FastAPI:
    return ApplicationFactory().create_app()


app = create_app()

if __name__ == "__main__":
    import uvicorn

    server = get_application_context().server
    uvicorn.run(
        "backend.app.app:app",
        host=server["host"],
        port=server["port"],
        reload=bool(server.get("reload", False)),
    )