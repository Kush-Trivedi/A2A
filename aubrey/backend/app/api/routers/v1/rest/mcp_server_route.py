"""Aubrey as an MCP server — the outbound half of the vendor gateway.

One stateless JSON-RPC endpoint (POST /api/v1/mcp) that lets any MCP
client — Copilot, Claude, a vendor platform, an IDE — call Aubrey's
governed capabilities as tools. The 2026-07-28 stateless dialect is
primary (no sessions, per-request self-description); the legacy
initialize handshake is answered too so older clients keep working
through the deprecation window.

Auth is a TEAM SERVICE TOKEN (Bearer): an MCP client credential is a team
credential, so it reaches exactly what that team's agents can reach —
same registry, same Casbin posture, same audit trail as everything else.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .....entity.agents import AgentStatus, RegisteredAgentEntity
from .....entity.documents import ConnectionType
from .....security.service_auth import require_service_token
from .....services.a2a.a2a_client_service import get_a2a_client_service
from .....services.a2a.context_envelope import ContextEnvelope
from .....services.data import get_data_query_service, get_text2sql_service
from .....services.knowledge.retrieval_service import get_retrieval_service
from .....utils.common.logger import Logger
from .....utils.errors import AppError

logger = Logger(__name__).get_logger()

mcp_server_router = APIRouter(prefix="/mcp", tags=["MCP Server"])

_MODERN = "2026-07-28"
_LEGACY = "2025-06-18"

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "ask_agent",
        "description": (
            "Ask one of the team's Aubrey agents a question in natural "
            "language and receive its grounded answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_key": {"type": "string", "description": "Registered agent key"},
                "question": {"type": "string"},
            },
            "required": ["agent_key", "question"],
        },
    },
    {
        "name": "retrieve_knowledge",
        "description": (
            "Grant-scoped document retrieval from an agent's ingested "
            "knowledge: ranked chunks with sources, no generation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_key": {"type": "string"},
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["agent_key", "query"],
        },
    },
    {
        "name": "query_data",
        "description": (
            "Natural-language question over the team's registered "
            "Databricks SQL connection (text-to-SQL fast lane)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection_key": {"type": "string"},
                "agent_key": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["connection_key", "agent_key", "question"],
        },
    },
]


def _envelope_for(token) -> ContextEnvelope:
    return ContextEnvelope(
        tenant_id=token.tenant_id,
        user_id=f"mcp-client:{token.team_key}",
        actor_id=f"mcp-client:{token.team_key}",
        roles=("service",),
        purpose="mcp",
    )


async def _owned_active_agent(token, agent_key: str) -> RegisteredAgentEntity:
    from .....security.service_auth import resolve_owned_agent

    return await resolve_owned_agent(token=token, agent_key=agent_key)


async def _tool_ask_agent(token, arguments: dict) -> str:
    agent = await _owned_active_agent(token, str(arguments.get("agent_key") or ""))
    chunks: list[str] = []
    async for event in get_a2a_client_service().stream_message(
        agent_key=agent.agent_key,
        card_url=agent.card_url,
        text=str(arguments.get("question") or ""),
        context_id=f"mcp-{token.team_key}",
        envelope=_envelope_for(token),
    ):
        if event.kind == "text" and event.text:
            chunks.append(event.text)
    return "".join(chunks).strip() or "(the agent returned no text)"


async def _tool_retrieve(token, arguments: dict) -> str:
    agent = await _owned_active_agent(token, str(arguments.get("agent_key") or ""))
    results = await get_retrieval_service().retrieve(
        tenant_id=token.tenant_id,
        agent_key=agent.agent_key,
        query=str(arguments.get("query") or ""),
        top_k=int(arguments["top_k"]) if arguments.get("top_k") else None,
    )
    lines = [
        f"[{c.file_name or c.source_uri}] (score {c.score:.2f})\n{c.content}"
        for c in results
    ]
    return "\n\n".join(lines) or "(no matching documents)"


async def _tool_query_data(token, arguments: dict) -> str:
    await _owned_active_agent(token, str(arguments.get("agent_key") or ""))
    connection = await get_data_query_service().resolve_connection(
        tenant_id=token.tenant_id,
        team_key=token.team_key,
        connection_key=str(arguments.get("connection_key") or ""),
        expected_type=ConnectionType.DATABRICKS_SQL,
    )
    answer = await get_text2sql_service().ask(
        connection=connection, question=str(arguments.get("question") or "")
    )
    if not answer.answerable:
        return f"Not answerable from this data: {answer.reason}"
    header = " | ".join(answer.columns)
    rows = "\n".join(" | ".join("" if v is None else str(v) for v in r) for r in answer.rows)
    return f"SQL: {answer.sql}\n{header}\n{rows}"


_HANDLERS = {
    "ask_agent": _tool_ask_agent,
    "retrieve_knowledge": _tool_retrieve,
    "query_data": _tool_query_data,
}


def _result(request_id, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


@mcp_server_router.post("", include_in_schema=False)
@mcp_server_router.post("/", include_in_schema=False)
async def mcp_endpoint(
    request: Request,
    token=Depends(require_service_token),
) -> Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error(None, -32700, "Parse error")
    method = str(payload.get("method") or "")
    request_id = payload.get("id")
    params = dict(payload.get("params") or {})

    if method == "initialize":  # legacy clients — answered, never required
        return _result(request_id, {
            "protocolVersion": _LEGACY,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aubrey", "version": "1.0"},
        })
    if method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "tools/list":
        return _result(request_id, {"tools": _TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        handler = _HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"Unknown tool '{name}'.")
        try:
            text = await handler(token, dict(params.get("arguments") or {}))
        except AppError as exc:
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": exc.client_message()}],
            })
        except Exception:  # noqa: BLE001
            logger.error("MCP tool failed", extra={"tool": name}, exc_info=True)
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": "The tool call failed."}],
            })
        logger.info(
            "Aubrey MCP tool served",
            extra={"tool": name, "team_key": token.team_key},
        )
        return _result(request_id, {
            "isError": False,
            "content": [{"type": "text", "text": text}],
        })
    return _error(request_id, -32601, f"Method '{method}' not supported.")
