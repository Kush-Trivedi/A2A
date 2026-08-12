"""Assembles a runnable A2A server from a manifest + an answer stream:
card routes + JSON-RPC + REST bindings, startup self-registration."""

import asyncio
from contextlib import asynccontextmanager, suppress

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from .capability_client import AubreyCapabilityClient
from .card import AgentCardBuilder
from .config import AgentConfig
from .executor import AnswerStream, KitAgentExecutor
from .registrar import AgentRegistrar


def build_agent_app(
    config: AgentConfig,
    answer_stream: AnswerStream,
    *,
    capability_client: AubreyCapabilityClient | None = None,
) -> Starlette:
    client = capability_client or AubreyCapabilityClient(
        base_url=config.aubrey_base_url,
        team_token=config.team_token,
        agent_key=config.agent_key,
    )
    registrar = AgentRegistrar(config, client)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        registration = asyncio.create_task(registrar.register_with_retry())
        try:
            yield
        finally:
            registration.cancel()
            with suppress(asyncio.CancelledError):
                await registration

    card = AgentCardBuilder(config).build()
    handler = DefaultRequestHandler(
        agent_executor=KitAgentExecutor(answer_stream),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, rpc_url="/"),
            *create_rest_routes(handler),
        ],
        lifespan=lifespan,
    )
