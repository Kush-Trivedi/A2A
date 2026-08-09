import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from .agent_executor import FileQaAgentExecutor
from .auth import EntraAuthMiddleware, EntraTokenValidator
from .card import AgentCardBuilder
from .config import AgentConfig, get_agent_config


class AgentApplicationBuilder:
    """Assembles the A2A server: card + RPC routes + optional Entra auth."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or get_agent_config()

    def build(self) -> Starlette:
        card = AgentCardBuilder(self._config).build()
        handler = DefaultRequestHandler(
            agent_executor=FileQaAgentExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        app = Starlette(
            routes=[
                *create_agent_card_routes(card),
                *create_jsonrpc_routes(handler, rpc_url="/"),
                *create_rest_routes(handler),
            ]
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
