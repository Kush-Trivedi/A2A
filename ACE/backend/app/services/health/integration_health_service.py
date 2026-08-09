import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from sqlalchemy import text

from ...config.application_context import ApplicationContext, get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...database.databricks.workspace_client_factory import (
    DatabricksWorkspaceClientFactory,
    get_workspace_client_factory,
)
from ...database.rdbms.pg_session import get_postgres_connector
from ...utils.azure.azure_helpers import AzureKeyVaultSecretStore
from ...utils.common.logger import Logger
from ..agents.registry_service import AgentRegistryService, get_agent_registry_service
from ..embedding.embedding_service import EmbeddingService, get_embedding_service

logger = Logger(__name__).get_logger()

_STATUS_OK = "ok"
_STATUS_ERROR = "error"
_STATUS_NOT_CONFIGURED = "not_configured"

_AGENT_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class IntegrationProbeResult:
    name: str
    status: str
    detail: str
    latency_ms: float


class IntegrationHealthService:
    """Live probes for every configured integration.

    The same probe code runs in every environment — only the yaml values
    differ. A probe never raises: it returns `ok`, `error` (with a sanitized
    reason), or `not_configured` (naming the yaml key still holding a
    placeholder). This endpoint is the checklist to run after swapping real
    credentials into the yaml files.
    """

    def __init__(
        self,
        context: ApplicationContext | None = None,
        registry_service: AgentRegistryService | None = None,
        embedding_service: EmbeddingService | None = None,
        databricks_factory: DatabricksWorkspaceClientFactory | None = None,
    ) -> None:
        self._context = context or get_application_context()
        self._registry_service = registry_service or get_agent_registry_service()
        self._embedding_service = embedding_service or get_embedding_service()
        self._databricks_factory = databricks_factory

    async def check_all(self) -> list[IntegrationProbeResult]:
        grouped = await asyncio.gather(
            self._timed("postgres", self._probe_postgres),
            self._timed("databricks", self._probe_databricks),
            self._timed("keyvault", self._probe_keyvault),
            self._timed("llm_embeddings", self._probe_llm),
            self._probe_registered_agents(),
        )
        results: list[IntegrationProbeResult] = []
        for item in grouped:
            if isinstance(item, list):
                results.extend(item)
            else:
                results.append(item)
        return results

    async def _timed(
        self, name: str, probe: Callable[[], Awaitable[tuple[str, str]]]
    ) -> IntegrationProbeResult:
        started = time.perf_counter()
        try:
            status, detail = await probe()
        except Exception as exc:  # noqa: BLE001 — probes must never raise
            status, detail = _STATUS_ERROR, self._sanitize(exc)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return IntegrationProbeResult(
            name=name, status=status, detail=detail, latency_ms=latency_ms
        )

    @staticmethod
    def _sanitize(exc: Exception) -> str:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return f"{type(exc).__name__}: {message[:300]}"

    async def _probe_postgres(self) -> tuple[str, str]:
        async with get_postgres_connector().connect() as connection:
            await connection.execute(text("SELECT 1"))
        return _STATUS_OK, "SELECT 1 succeeded."

    async def _probe_databricks(self) -> tuple[str, str]:
        databricks = self._context.databricks
        host = databricks.get("host")
        token = databricks.get("token")
        if not PlaceholderPolicy.is_configured(host):
            return _STATUS_NOT_CONFIGURED, "Set databricks.host in the env yaml."
        if not PlaceholderPolicy.is_configured(token):
            return _STATUS_NOT_CONFIGURED, "Set databricks.token (PAT) in the env yaml."

        factory = self._databricks_factory or get_workspace_client_factory()
        healthy = await asyncio.to_thread(factory.health_check)
        if healthy:
            return _STATUS_OK, "Workspace reachable; PAT accepted (current_user.me)."
        return _STATUS_ERROR, "Workspace client call failed — check databricks.host/token."

    async def _probe_keyvault(self) -> tuple[str, str]:
        keyvault = self._context.microsoft.get("azure", {}).get("keyvault", {})
        vault_url = keyvault.get("keyvault_url")
        if not PlaceholderPolicy.is_configured(vault_url):
            return (
                _STATUS_NOT_CONFIGURED,
                "Set microsoft.azure.keyvault.keyvault_url in the env yaml.",
            )

        def _acquire_token() -> None:
            store = AzureKeyVaultSecretStore(
                vault_url=str(vault_url),
                secret_prefix=str(keyvault.get("keyvault_secret_prefix") or ""),
                managed_identity_client_id=self._context.managed_identity_client_id or None,
            )
            store.credential.get_token("https://vault.azure.net/.default")

        await asyncio.to_thread(_acquire_token)
        return _STATUS_OK, "Azure credential acquired a Key Vault token."

    async def _probe_llm(self) -> tuple[str, str]:
        foundry = self._context.microsoft.get("azure", {}).get("azure_foundry", {})
        if not PlaceholderPolicy.is_configured(foundry.get("base_endpoint")):
            return (
                _STATUS_NOT_CONFIGURED,
                "Set microsoft.azure.azure_foundry.base_endpoint in the env yaml.",
            )
        if not PlaceholderPolicy.is_configured(foundry.get("api_key")):
            return (
                _STATUS_NOT_CONFIGURED,
                "Set microsoft.azure.azure_foundry.api_key in the env yaml.",
            )

        vectors = await self._embedding_service.embed_texts(["ping"])
        if vectors and vectors[0]:
            return (
                _STATUS_OK,
                f"Embedding call succeeded (model={self._embedding_service.model}).",
            )
        return _STATUS_ERROR, "Embedding call returned no vectors."

    async def _probe_registered_agents(self) -> list[IntegrationProbeResult]:
        started = time.perf_counter()
        try:
            cards = await self._registry_service.list_active_agent_cards()
        except Exception as exc:  # noqa: BLE001
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            return [
                IntegrationProbeResult(
                    name="a2a_agents",
                    status=_STATUS_ERROR,
                    detail=self._sanitize(exc),
                    latency_ms=latency_ms,
                )
            ]

        if not cards:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            return [
                IntegrationProbeResult(
                    name="a2a_agents",
                    status=_STATUS_NOT_CONFIGURED,
                    detail="No active A2A agents with a card_url are registered.",
                    latency_ms=latency_ms,
                )
            ]

        async with httpx.AsyncClient(timeout=_AGENT_PROBE_TIMEOUT_SECONDS) as client:
            return list(
                await asyncio.gather(
                    *(self._probe_agent_card(client, key, url) for key, url in cards)
                )
            )

    async def _probe_agent_card(
        self, client: httpx.AsyncClient, agent_key: str, card_url: str
    ) -> IntegrationProbeResult:
        started = time.perf_counter()
        try:
            response = await client.get(card_url)
            response.raise_for_status()
            card = response.json()
            detail = (
                f"Card reachable (name={card.get('name', '?')}, "
                f"protocol={card.get('protocolVersion', '?')})."
            )
            status = _STATUS_OK
        except Exception as exc:  # noqa: BLE001
            status, detail = _STATUS_ERROR, self._sanitize(exc)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return IntegrationProbeResult(
            name=f"agent:{agent_key}", status=status, detail=detail, latency_ms=latency_ms
        )


_service: IntegrationHealthService | None = None


def get_integration_health_service() -> IntegrationHealthService:
    global _service
    if _service is None:
        _service = IntegrationHealthService()
    return _service
