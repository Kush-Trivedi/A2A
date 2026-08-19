"""The MCP gateway — how team agents consume third-party vendor tools.

Vendors register as connections (source_type "mcp"); credentials stay in
the connection config, platform-side. Agents never see vendor URLs or
secrets: they ask the capability plane to list or call tools by
connection_key, and every call is logged with team, agent, and tool name.
This is the buy-now lane of the vendor gateway: a purchased capability is
one connection row away from every agent its team runs."""

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.documents import ConnectionEntity
from ...utils.common.logger import Logger
from ...utils.mcp import McpClient

logger = Logger(__name__).get_logger()


class McpGatewayService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()
        # one initialized client per connection id, rebuilt on config change
        self._clients: dict[str, tuple[str, McpClient]] = {}

    async def _client_for(self, connection: ConnectionEntity) -> McpClient:
        config = dict(connection.config or {})
        server_url = str(config.get("server_url") or "")
        header_name = str(config.get("auth_header_name") or "Authorization")
        header_value = str(config.get("auth_header_value") or "")
        fingerprint = f"{server_url}|{header_name}|{header_value}"
        cached = self._clients.get(connection.id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        protocol = str(config.get("protocol") or "auto")  # auto | 2026-07-28 | legacy
        headers = {header_name: header_value} if header_value else {}
        client = McpClient(server_url=server_url, headers=headers, protocol=protocol)
        self._clients[connection.id] = (fingerprint, client)
        return client

    async def list_tools(self, *, connection: ConnectionEntity) -> list[dict]:
        client = await self._client_for(connection)
        tools = await client.list_tools()
        logger.info(
            "MCP tools listed",
            extra={"connection_key": connection.connection_key, "count": len(tools)},
        )
        return [
            {
                "name": str(t.get("name") or ""),
                "description": str(t.get("description") or ""),
                "input_schema": t.get("inputSchema") or {},
            }
            for t in tools
        ]

    async def call_tool(
        self, *, connection: ConnectionEntity, agent_key: str, tool: str, arguments: dict
    ) -> dict:
        client = await self._client_for(connection)
        result = await client.call_tool(name=tool, arguments=arguments)
        logger.info(
            "MCP tool called",
            extra={
                "connection_key": connection.connection_key,
                "agent_key": agent_key,
                "tool": tool,
                "is_error": bool(result.get("isError")),
            },
        )
        # normalize: content list of {type,text}; join text parts for agents
        parts = [
            str(item.get("text") or "")
            for item in (result.get("content") or [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return {
            "is_error": bool(result.get("isError")),
            "text": "\n".join(p for p in parts if p),
            "structured": result.get("structuredContent") or {},
        }


_service: McpGatewayService | None = None


def get_mcp_gateway_service() -> McpGatewayService:
    global _service
    if _service is None:
        _service = McpGatewayService()
    return _service
