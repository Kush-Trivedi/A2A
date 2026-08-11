import asyncio
from contextlib import asynccontextmanager, suppress

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from ace_agent_kit import AgentRegistrar

from .agent_executor import TeamAgentExecutor
from .auth import EntraAuthMiddleware, EntraTokenValidator
from .card import AgentCardBuilder
from .config import AgentConfig, get_agent_config


@asynccontextmanager
async def _lifespan(app: Starlette):
    """Self-register with ACE on startup (bounded retry, idempotent) — start
    ACE and the agent in any order; identical flow local and cloud."""
    registration = asyncio.create_task(AgentRegistrar().register_with_retry())
    try:
        yield
    finally:
        registration.cancel()
        with suppress(asyncio.CancelledError):
            await registration


class AgentApplicationBuilder:
    """Assembles the A2A server: card + RPC routes + optional Entra auth +
    startup self-registration via the kit registrar."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or get_agent_config()

    def build(self) -> Starlette:
        card = AgentCardBuilder(self._config).build()
        handler = DefaultRequestHandler(
            agent_executor=TeamAgentExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        app = Starlette(
            routes=[
                *create_agent_card_routes(card),
                *create_jsonrpc_routes(handler, rpc_url="/"),
                *create_rest_routes(handler),
            ],
            lifespan=_lifespan,
        )
        if self._config.auth.enabled:
            app.add_middleware(
                EntraAuthMiddleware,
                validator=EntraTokenValidator(self._config.auth),
            )
        return app


app = AgentApplicationBuilder().build()

if __name__ == "__main__":
    config = get_agent_config()
    uvicorn.run("app.main:app", host=config.host, port=config.port)
