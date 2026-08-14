"""Databricks workspace clients — the ACE factory pattern, upgraded.

Differences from the ACE original (single global client, sync SDK):
- MULTI-workspace: the yaml `databricks.workspaces` map can hold several
  workspaces; the factory caches one client per key. Teams pick a
  workspace by name in their connection config — never by credential.
- Async httpx REST instead of the sync databricks-sdk (same decision as
  the Twilio client: no heavyweight dependency, native async).
- Authentication is PAT-only across every environment (standing decision).

The client speaks exactly two API families: Genie Conversations
(/api/2.0/genie/*) and SQL Statement Execution (/api/2.0/sql/statements).
"""

import threading
from typing import Any

import httpx

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError

logger = Logger(__name__).get_logger()

_REQUEST_TIMEOUT = 60.0


class DatabricksRestClient:
    """One workspace, PAT-authenticated, async."""

    def __init__(self, *, host: str, token: str) -> None:
        self._base = host.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _call(
        self, method: str, path: str, *, json: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            try:
                response = await client.request(
                    method, f"{self._base}{path}",
                    headers=self._headers, json=json, params=params,
                )
            except httpx.HTTPError as exc:
                raise ExternalServiceError(
                    "Databricks could not be reached.", cause=exc
                ) from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("message") or "")[:300]
            except Exception:  # noqa: BLE001
                pass
            logger.error(
                "Databricks API call failed",
                extra={"path": path, "http": response.status_code, "detail": detail},
            )
            raise ExternalServiceError(
                f"Databricks rejected the request ({response.status_code}): {detail}"
            )
        return dict(response.json() or {})

    # --- Genie Conversations ------------------------------------------- #

    async def genie_start_conversation(self, *, space_id: str, content: str) -> dict:
        return await self._call(
            "POST", f"/api/2.0/genie/spaces/{space_id}/start-conversation",
            json={"content": content},
        )

    async def genie_create_message(
        self, *, space_id: str, conversation_id: str, content: str
    ) -> dict:
        return await self._call(
            "POST",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages",
            json={"content": content},
        )

    async def genie_get_message(
        self, *, space_id: str, conversation_id: str, message_id: str
    ) -> dict:
        return await self._call(
            "GET",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}"
            f"/messages/{message_id}",
        )

    async def genie_get_query_result(
        self, *, space_id: str, conversation_id: str, message_id: str, attachment_id: str
    ) -> dict:
        return await self._call(
            "GET",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}"
            f"/messages/{message_id}/attachments/{attachment_id}/query-result",
        )

    # --- SQL Statement Execution --------------------------------------- #

    async def sql_execute(
        self,
        *,
        warehouse_id: str,
        statement: str,
        catalog: str | None = None,
        schema: str | None = None,
        wait_timeout: str = "50s",
        row_limit: int = 100,
    ) -> dict:
        payload: dict[str, Any] = {
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": wait_timeout,
            "on_wait_timeout": "CANCEL",
            "row_limit": row_limit,
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        }
        if catalog:
            payload["catalog"] = catalog
        if schema:
            payload["schema"] = schema
        return await self._call("POST", "/api/2.0/sql/statements", json=payload)


class DatabricksWorkspaceClientFactory:
    """Builds and caches one client per yaml workspace key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, DatabricksRestClient] = {}

    def get_client(self, workspace_key: str) -> DatabricksRestClient:
        key = (workspace_key or "").strip().lower()
        cached = self._clients.get(key)
        if cached is not None:
            return cached
        workspaces = get_application_context().databricks.get("workspaces") or {}
        config = workspaces.get(key)
        if config is None:
            raise ValidationError(
                f"Unknown Databricks workspace '{key}'. Configure it under "
                "databricks.workspaces in the env yaml.",
                details={"known": sorted(workspaces)},
            )
        host = str(config.get("host") or "")
        token = str(config.get("token") or "")
        for name, value in (("host", host), ("token", token)):
            if not PlaceholderPolicy.is_configured(value):
                raise ValidationError(
                    f"Databricks workspace '{key}' is not configured. Set "
                    f"databricks.workspaces.{key}.{name} in the env yaml."
                )
        with self._lock:
            if key not in self._clients:
                self._clients[key] = DatabricksRestClient(host=host, token=token)
                logger.info(
                    "Databricks workspace client created (PAT auth)",
                    extra={"workspace": key},
                )
        return self._clients[key]

    def reset(self) -> None:
        with self._lock:
            self._clients.clear()


_factory: DatabricksWorkspaceClientFactory | None = None


def get_workspace_client_factory() -> DatabricksWorkspaceClientFactory:
    global _factory
    if _factory is None:
        _factory = DatabricksWorkspaceClientFactory()
    return _factory
