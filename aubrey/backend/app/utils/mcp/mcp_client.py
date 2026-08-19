"""Minimal MCP client — JSON-RPC 2.0 over streamable HTTP via httpx, no
SDK dependency. Dual-dialect and future proof:

- "2026-07-28" (current spec): STATELESS — no initialize handshake, no
  Mcp-Session-Id; every request carries MCP-Protocol-Version, Mcp-Method
  and Mcp-Name headers plus client identity in params._meta. Tool calls
  may return resultType "input_required" (MRTR).
- "legacy" (2025-06-18 dialect): initialize handshake + Mcp-Session-Id,
  still widespread during the 12-month deprecation window.
- "auto" (default): try the current dialect first, fall back to legacy
  once, and remember what worked.

Handles both plain JSON and SSE-framed responses."""

import json
from typing import Any

import httpx

from ..common.logger import Logger
from ..errors import ExternalServiceError

logger = Logger(__name__).get_logger()

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "aubrey-gateway", "version": "1.0"}


class McpClient:
    def __init__(
        self,
        *,
        server_url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 60.0,
        protocol: str = "auto",  # auto | 2026-07-28 | legacy
    ) -> None:
        self._url = server_url
        self._base_headers = {
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._timeout = timeout_seconds
        self._protocol = protocol
        self._session_id: str | None = None
        self._initialized = False
        self._request_id = 0

    # ------------------------------------------------------------------ #

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._call_with_dialect("tools/list", None, name="")
        return list(result.get("tools") or [])

    async def call_tool(self, *, name: str, arguments: dict) -> dict[str, Any]:
        return await self._call_with_dialect(
            "tools/call", {"name": name, "arguments": arguments}, name=name
        )

    # ------------------------------------------------------------------ #

    async def _call_with_dialect(
        self, method: str, params: dict | None, *, name: str
    ) -> dict[str, Any]:
        if self._protocol == "legacy":
            return await self._legacy_call(method, params)
        if self._protocol == MODERN_VERSION:
            return await self._modern_call(method, params, name=name)
        # auto: modern first, legacy fallback, then pin whichever worked
        try:
            result = await self._modern_call(method, params, name=name)
            self._protocol = MODERN_VERSION
            return result
        except ExternalServiceError:
            logger.info("MCP modern dialect failed — retrying legacy", extra={"method": method})
            result = await self._legacy_call(method, params)
            self._protocol = "legacy"
            return result

    async def _modern_call(
        self, method: str, params: dict | None, *, name: str
    ) -> dict[str, Any]:
        merged = dict(params or {})
        merged["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientInfo": _CLIENT_INFO,
        }
        headers = {
            "MCP-Protocol-Version": MODERN_VERSION,
            "Mcp-Method": method,
        }
        if name:
            headers["Mcp-Name"] = name
        return await self._post(method, merged, extra_headers=headers)

    async def _legacy_call(self, method: str, params: dict | None) -> dict[str, Any]:
        if not self._initialized:
            await self._post(
                "initialize",
                {
                    "protocolVersion": LEGACY_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            )
            self._initialized = True
            try:
                headers = dict(self._base_headers)
                if self._session_id:
                    headers["Mcp-Session-Id"] = self._session_id
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    await client.post(
                        self._url, headers=headers,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )
            except Exception:  # noqa: BLE001 — courtesy notification only
                pass
        return await self._post(method, params)

    async def _post(
        self, method: str, params: dict | None, extra_headers: dict | None = None
    ) -> dict[str, Any]:
        self._request_id += 1
        headers = dict(self._base_headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        headers.update(extra_headers or {})
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            payload["params"] = params
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(self._url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise ExternalServiceError(
                    "The MCP server could not be reached.", cause=exc
                ) from exc
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"MCP server rejected the request ({response.status_code})."
            )
        session = response.headers.get("Mcp-Session-Id")
        if session:
            self._session_id = session
        body = self._parse_body(response)
        if "error" in body:
            error = body["error"] or {}
            raise ExternalServiceError(
                f"MCP error: {error.get('message') or error.get('code') or 'unknown'}"
            )
        return dict(body.get("result") or {})

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        text = response.text or ""
        if "text/event-stream" in content_type:
            for line in reversed(text.splitlines()):
                if line.startswith("data:"):
                    try:
                        return dict(json.loads(line[len("data:"):].strip()))
                    except Exception:  # noqa: BLE001
                        continue
            return {}
        try:
            return dict(response.json() or {})
        except Exception:  # noqa: BLE001
            return {}
