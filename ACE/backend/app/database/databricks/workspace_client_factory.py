import threading

from databricks.sdk import WorkspaceClient

from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError
from .databricks_settings import DatabricksSettings, get_databricks_settings

logger = Logger(__name__).get_logger()


class DatabricksWorkspaceClientFactory:
    """Builds and caches Databricks WorkspaceClients.

    Authentication is PAT-only across every environment; host and token come
    exclusively from the yaml `databricks` section via ApplicationContext.
    Team-specific resources (warehouse id, catalog, genie space) are NOT held
    here — they belong to each team's agent configuration.
    """

    def __init__(self, settings: DatabricksSettings | None = None) -> None:
        self._settings = settings or get_databricks_settings()
        self._lock = threading.Lock()
        self._client: WorkspaceClient | None = None

    def get_client(self) -> WorkspaceClient:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        self._client = WorkspaceClient(
                            host=self._settings.host,
                            token=self._settings.token,
                            auth_type="pat",
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to create Databricks WorkspaceClient",
                            extra={"error_code": "databricks_client_creation_failed"},
                            exc_info=True,
                        )
                        raise ExternalServiceError(
                            "Failed to create Databricks WorkspaceClient.", cause=exc
                        ) from exc
                    logger.info("[blue]Databricks WorkspaceClient created (PAT auth)")
        return self._client

    def health_check(self) -> bool:
        try:
            self.get_client().current_user.me()
            return True
        except Exception:
            logger.warning(
                "Databricks health check failed",
                extra={"error_code": "databricks_health_check_failed"},
                exc_info=True,
            )
            return False

    def reset(self) -> None:
        with self._lock:
            self._client = None


_factory: DatabricksWorkspaceClientFactory | None = None


def get_workspace_client_factory() -> DatabricksWorkspaceClientFactory:
    global _factory
    if _factory is None:
        _factory = DatabricksWorkspaceClientFactory()
    return _factory


def get_workspace_client() -> WorkspaceClient:
    return get_workspace_client_factory().get_client()
