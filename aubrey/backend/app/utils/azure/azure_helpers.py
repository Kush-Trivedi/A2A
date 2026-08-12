import threading
import time
from typing import Optional
from urllib.parse import unquote, urlparse

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
from openai import AsyncAzureOpenAI, AzureOpenAI

from ..common.logger import Logger
from ..errors import ValidationError

logger = Logger(__name__).get_logger()


class AzureKeyVaultSecretStore:
    """Key Vault secret lookup for `lookup:<name>` config values.

    Secret names: underscores become hyphens; an optional prefix is applied
    (supports a literal `{key}` slot, otherwise `<prefix>-<key>`).
    """

    def __init__(
        self,
        vault_url: Optional[str] = None,
        secret_prefix: Optional[str] = None,
        managed_identity_client_id: Optional[str] = None,
    ) -> None:
        if not vault_url:
            raise ValueError(
                "Azure Key Vault URL is required. Set microsoft.azure.keyvault.keyvault_url in YAML."
            )
        self.vault_url = vault_url
        self.secret_prefix = secret_prefix or ""
        self.credential = DefaultAzureCredential(
            managed_identity_client_id=managed_identity_client_id,
        )
        self.client = SecretClient(vault_url=self.vault_url, credential=self.credential)

    def _full_secret_name(self, key: str) -> str:
        normalized_key = key.replace("_", "-")
        prefix = self.secret_prefix.strip().replace("_", "-")
        if not prefix:
            return normalized_key
        if "{key}" in prefix:
            return prefix.format(key=normalized_key)
        separator = "" if prefix.endswith("-") else "-"
        return f"{prefix}{separator}{normalized_key}"

    def get_secret(self, key: str) -> str:
        secret_name = self._full_secret_name(key)
        try:
            secret = self.client.get_secret(secret_name)
        except ResourceNotFoundError:
            logger.error("Key Vault secret not found: %s", secret_name)
            raise
        except AzureError:
            logger.error("Key Vault secret retrieval failed: %s", secret_name)
            raise
        return secret.value or ""


class AzureOpenAIClient:
    """Foundry client factory: ONE base endpoint + api key, clients cached
    per (deployment, api_version) — teams differ only by deployment name."""

    def __init__(
        self,
        api_key: str,
        base_endpoint: str,
        deployment: str | None = None,
        api_version: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.api_key = api_key
        self.base_endpoint = base_endpoint
        self.deployment = deployment
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self._sync_clients: dict[tuple[str | None, str | None], AzureOpenAI] = {}
        self._async_clients: dict[tuple[str | None, str | None], AsyncAzureOpenAI] = {}

    def get_client(
        self, deployment: str | None = None, api_version: str | None = None
    ) -> AzureOpenAI:
        resolved_api_version = api_version or self.api_version
        cache_key = (deployment or self.deployment, resolved_api_version)
        if cache_key not in self._sync_clients:
            self._sync_clients[cache_key] = AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.base_endpoint,
                api_version=resolved_api_version,
                timeout=self.timeout_seconds,
            )
        return self._sync_clients[cache_key]

    def get_async_client(
        self, deployment: str | None = None, api_version: str | None = None
    ) -> AsyncAzureOpenAI:
        resolved_api_version = api_version or self.api_version
        cache_key = (deployment or self.deployment, resolved_api_version)
        if cache_key not in self._async_clients:
            self._async_clients[cache_key] = AsyncAzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.base_endpoint,
                api_version=resolved_api_version,
                timeout=self.timeout_seconds,
            )
        return self._async_clients[cache_key]


class AzureStorageBlobClient:
    """Blob access for ingestion: recursive listing (a prefix walks folders
    inside folders) and per-blob download — by (container, name) or by the
    blob's full URL (the Event Grid case: a URL arrives, we fetch one file)."""

    def __init__(self, account_url: str, managed_identity_client_id: Optional[str] = None):
        if not account_url:
            raise ValidationError("Azure Storage account_url is required.")
        self.account_url = account_url.rstrip("/")
        self._client = BlobServiceClient(
            account_url=self.account_url,
            credential=DefaultAzureCredential(
                managed_identity_client_id=managed_identity_client_id
            ),
        )

    def list_blobs(self, container: str, prefix: str | None = None) -> list[str]:
        """All blob names under the prefix — Azure listing is inherently
        recursive, so nested 'folders' are included."""
        container_client = self._client.get_container_client(container)
        return [blob.name for blob in container_client.list_blobs(name_starts_with=prefix)]

    def read_blob_bytes(self, container: str, blob_name: str) -> bytes:
        blob_client = self._client.get_blob_client(container=container, blob=blob_name)
        return blob_client.download_blob().readall()

    def parse_blob_url(self, url: str) -> tuple[str, str]:
        """(container, blob_name) from a full blob URL on THIS account."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin.rstrip("/") != self.account_url:
            raise ValidationError(
                "The blob URL does not belong to the configured storage account.",
                details={"url_account": origin, "configured": self.account_url},
            )
        path = unquote(parsed.path).lstrip("/")
        container, _, blob_name = path.partition("/")
        if not container or not blob_name:
            raise ValidationError(
                "The blob URL must be https://<account>/<container>/<blob-path>.",
                details={"url": url},
            )
        return container, blob_name

    def read_blob_url(self, url: str) -> tuple[str, bytes]:
        """(blob_name, content) for a full blob URL."""
        container, blob_name = self.parse_blob_url(url)
        return blob_name, self.read_blob_bytes(container, blob_name)


class AzurePostgresToken:
    """Entra access token for Postgres AAD auth (database.postgres.auth_mode
    = 'entra'), cached and refreshed shortly before expiry."""

    _SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
    _REFRESH_SKEW_SECONDS = 60

    def __init__(self, managed_identity_client_id: Optional[str] = None) -> None:
        self._credential = DefaultAzureCredential(
            managed_identity_client_id=managed_identity_client_id,
        )
        self._lock = threading.Lock()
        self._cached_token: Optional[str] = None
        self._cached_expires_on: float = 0.0

    def generate_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._cached_token and now < (self._cached_expires_on - self._REFRESH_SKEW_SECONDS):
                return self._cached_token
            access_token = self._credential.get_token(self._SCOPE)
            self._cached_token = access_token.token
            self._cached_expires_on = access_token.expires_on
            return self._cached_token
