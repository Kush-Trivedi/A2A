from contextlib import asynccontextmanager

from fastapi import FastAPI, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

from .api.exception_handlers import register_exception_handlers
from .api.routers.health_check_route import health_check_router
from .api.routers.v1 import oauth_compact_router, v1_router
from .config.application_context import get_application_context
from .config.settings_validator import get_settings_validator
from .database.rdbms.pg_session import dispose_postgres
from .scripts.init_db import initialize_database_lifecycle
from .security.authorization import get_casbin_enforcer
from .services.memory import get_memory_decay_scheduler
from .utils.common.logger import Logger

logger = Logger(__name__).get_logger()

# Shown as the Authorize button in Swagger: log in via /api/v1/auth/login in
# the browser, call /api/v1/auth/me here, paste the csrf_token — every
# endpoint is then testable from /docs. The session cookie flows
# automatically (same origin); this header is the CSRF half.
csrf_header = APIKeyHeader(name="X-CSRF-Token", auto_error=False)


class ApplicationFactory:
    def __init__(self) -> None:
        self._context = get_application_context()
        self._enforcer = get_casbin_enforcer()

    def _lifespan(self):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            get_settings_validator().validate_and_log()
            logger.info("Startup: initializing database + authorization")
            await initialize_database_lifecycle()
            await self._enforcer.initialize()
            # M10b: append+decay maintenance — memory weights age and prune,
            # expired browser sessions purge, on the yaml interval.
            decay = get_memory_decay_scheduler()
            decay.start()
            try:
                yield
            finally:
                logger.info("Shutdown: releasing resources")
                await decay.stop()
                await self._enforcer.close()
                await dispose_postgres()

        return lifespan

    def create(self) -> FastAPI:
        app = FastAPI(
            title="Aubrey — ACE Control Plane",
            version="0.1.0",
            lifespan=self._lifespan(),
            dependencies=[Security(csrf_header)],
            swagger_ui_parameters={"persistAuthorization": True},
        )

        cors = self._context.security.get("cors", {}) or {}
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors.get("allowed_origins") or []),
            allow_credentials=bool(cors.get("allow_credentials", True)),
            allow_methods=["*"],
            allow_headers=["*"],
        )

        register_exception_handlers(app)
        app.include_router(health_check_router)
        app.include_router(v1_router)
        app.include_router(oauth_compact_router)
        return app


app = ApplicationFactory().create()


if __name__ == "__main__":
    import uvicorn

    server = get_application_context().server
    reload = bool(server.get("reload", False))
    # Python scales with processes: yaml `workers` fans out uvicorn workers
    # on one box; the API is stateless (all state in Postgres), so replicas
    # behind a load balancer scale it horizontally beyond that.
    workers = int(server["workers"])
    uvicorn.run(
        "backend.app.app:app",
        host=server["host"],
        port=server["port"],
        reload=reload,
        workers=None if reload else workers,
    )
