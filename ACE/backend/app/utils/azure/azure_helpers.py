import time
import threading
from ..common.logger import Logger
from typing import Optional, List, Union
from azure.keyvault.secrets import SecretClient
from openai import AsyncAzureOpenAI, AzureOpenAI
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import AzureError, ResourceNotFoundError


logger = Logger(__name__).get_logger()

class AzureKeyVaultSecretStore:
    def __init__(
        self,
        vault_url: Optional[str] = None,
        secret_prefix: Optional[str] = None,
        managed_identity_client_id: Optional[str] = None,
    ):
        self.vault_url = vault_url

        if not self.vault_url:
            raise ValueError("Azure Key Vault URL is required. Set microsoft.azure.keyvault.keyvault_url in YAML.")

        self.secret_prefix = secret_prefix or ""

        self.credential = DefaultAzureCredential(
            managed_identity_client_id=managed_identity_client_id,
        )
        self.client = SecretClient(
            vault_url=self.vault_url,
            credential=self.credential,
        )

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
        return self.get_secret_by_name(secret_name)

    def get_secret_by_name(self, secret_name: str) -> str:
        try:
            secret = self.client.get_secret(secret_name)
            return secret.value or ""

        except ResourceNotFoundError as exc:
            logger.warning(
                "Azure Key Vault secret was not found",
                extra={"error_code": "keyvault_secret_not_found"},
            )
            logger.debug("Azure Key Vault secret lookup debug details", exc_info=True)
            raise

        except AzureError as e:
            logger.error(
                "Failed to retrieve secret from Azure Key Vault",
                extra={"error_code": "keyvault_secret_retrieval_failed"},
                exc_info=False,
            )
            logger.debug("Azure Key Vault secret retrieval debug details", exc_info=True)
            raise


class AzurePostgresToken:
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

            try:
                access_token = self._credential.get_token(self._SCOPE)
            except Exception:
                logger.error(
                    "Failed to acquire Azure AD token for PostgreSQL",
                    extra={"error_code": "azure_postgres_token_acquire_failed"},
                    exc_info=True,
                )
                raise

            self._cached_token = access_token.token
            self._cached_expires_on = access_token.expires_on
            return self._cached_token


class AzureOpenAIClient:
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

    def get_client(self, deployment: str | None = None, api_version: str | None = None) -> AzureOpenAI:
        resolved_api_version = api_version or self.api_version
        cache_key = (deployment or self.deployment, resolved_api_version)

        if cache_key not in self._sync_clients:
            try:
                self._sync_clients[cache_key] = AzureOpenAI(
                    api_key=self.api_key,
                    azure_endpoint=self.base_endpoint,
                    api_version=resolved_api_version,
                    timeout=self.timeout_seconds,
                )
            except Exception:
                logger.error(
                    "Failed to create Azure OpenAI client",
                    extra={"error_code": "azure_openai_client_creation_failed"},
                    exc_info=True,
                )
                raise

        return self._sync_clients[cache_key]

    def get_async_client(self, deployment: str | None = None, api_version: str | None = None) -> AsyncAzureOpenAI:
        resolved_api_version = api_version or self.api_version
        cache_key = (deployment or self.deployment, resolved_api_version)

        if cache_key not in self._async_clients:
            try:
                self._async_clients[cache_key] = AsyncAzureOpenAI(
                    api_key=self.api_key,
                    azure_endpoint=self.base_endpoint,
                    api_version=resolved_api_version,
                    timeout=self.timeout_seconds,
                )
            except Exception:
                logger.error(
                    "Failed to create async Azure OpenAI client",
                    extra={"error_code": "azure_openai_async_client_creation_failed"},
                    exc_info=True,
                )
                raise

        return self._async_clients[cache_key]


class AzureStorageBlobClient:
    def __init__(self, account_url: str, managed_identity_client_id: Optional[str] = None):
        self.account_url = account_url
        if not self.account_url:
            raise ValueError("Azure Storage Blob account URL is required.")

        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=DefaultAzureCredential(managed_identity_client_id=managed_identity_client_id),
        )

    def _container_client(self, container_name: str):
        return self.blob_service_client.get_container_client(container=container_name)

    def get_blob_metadata_hash(self, container_name: str, blob_name: str) -> str | None:
        try:
            container_client = self._container_client(container_name)
            blob_client = container_client.get_blob_client(blob_name)
            properties = blob_client.get_blob_properties()
            return properties.metadata.get("content_hash")
        except Exception:
            return None

    def tag_blob_with_hash(self, container_name: str, blob_name: str, content_hash: str):
        try:
            container_client = self._container_client(container_name)
            blob_client = container_client.get_blob_client(blob_name)
            metadata = blob_client.get_blob_properties().metadata or {}
            metadata["content_hash"] = content_hash
            blob_client.set_blob_metadata(metadata=metadata)
        except Exception as e:
            logger.warning(f"Could not write custom metadata tag to Azure for {blob_name}: {e}")

    def list_blobs(self, container_names: Union[str, List[str]], folder_prefix: str | None = None) -> list[tuple[str, str]]:
        containers = [container_names] if isinstance(container_names, str) else container_names
        all_blobs = []

        for container in containers:
            try:
                container_client = self._container_client(container)
                blob_list = container_client.list_blobs(name_starts_with=folder_prefix)
                for blob in blob_list:
                    all_blobs.append((container, blob.name))
            except Exception as e:
                logger.error(
                    f"Failed to list blobs in container '{container}'",
                    extra={"error_code": "azure_blob_list_failed"},
                    exc_info=True,
                )
                raise
        return all_blobs

    def read_blob_text(self, container_name: str, blob_name: str) -> str:
        try:
            container_client = self._container_client(container_name)
            blob_data = container_client.download_blob(blob_name).readall().decode("utf-8")
            return blob_data
        except Exception as e:
            logger.error(
                f"Failed to read blob text for '{blob_name}' in container '{container_name}'",
                extra={"error_code": "azure_blob_read_failed"},
                exc_info=True,
            )
            raise

    def read_blob_bytes(self, container_name: str, blob_name: str) -> bytes:
        try:
            container_client = self._container_client(container_name)
            blob_data = container_client.download_blob(blob_name).readall()
            return blob_data
        except Exception as e:
            logger.error(
                f"Failed to read blob bytes for '{blob_name}' in container '{container_name}'",
                extra={"error_code": "azure_blob_read_failed"},
                exc_info=True,
            )
            raise
