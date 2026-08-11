import asyncio
from dataclasses import dataclass

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...security.session import SessionContext
from ...utils.azure.azure_helpers import AzureStorageBlobClient
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from ..embedding.ingestion_service import IngestionService, get_ingestion_service

logger = Logger(__name__).get_logger()

_SOURCE_PREFIX = "blob:"


@dataclass(frozen=True)
class BlobIngestionResult:
    knowledge_source: str
    files_ingested: int
    files_skipped: int
    chunk_count: int


class BlobIngestionService:
    """Phase A of the blob storage agent: team-driven bulk load into pgvector.

    The team supplies container + prefix (their ownership); ACE supplies the
    storage account connection (yaml `microsoft.azure.storage_account`).
    Documents land under `blob:<source_name>` — write permission enforced by
    the ingestion pipeline, read permission at query time by the gateway.
    """

    def __init__(self, ingestion: IngestionService | None = None) -> None:
        self._ingestion = ingestion or get_ingestion_service()

    def _client(self, connection_config: dict | None = None) -> AzureStorageBlobClient:
        context = get_application_context()
        if connection_config:
            storage = dict(connection_config)
        else:
            storage = context.microsoft.get("azure", {}).get("storage_account", {}) or {}
        account_url = storage.get("account_url") or storage.get("storage_account_url")
        if not PlaceholderPolicy.is_configured(account_url):
            hint = (
                "connection config key 'account_url'"
                if connection_config
                else "microsoft.azure.storage_account.storage_account_url in the env yaml "
                "(or register a storage_blob connection)"
            )
            raise ValidationError(f"Blob storage is not configured. Set {hint}.")
        return AzureStorageBlobClient(
            account_url=str(account_url),
            managed_identity_client_id=context.managed_identity_client_id or None,
        )

    async def ingest_container(
        self,
        *,
        context: SessionContext,
        source_name: str,
        container: str,
        prefix: str = "",
        chunking_strategy: str | None = None,
        connection_config: dict | None = None,
    ) -> BlobIngestionResult:
        normalized = (source_name or "").strip().lower()
        if not normalized:
            raise ValidationError("source_name is required.")
        if not (container or "").strip():
            raise ValidationError("container is required.")
        knowledge_source = f"{_SOURCE_PREFIX}{normalized}"

        files = await asyncio.to_thread(self._list_files, container, prefix, connection_config)
        ingested = skipped = chunks = 0
        for filename, raw_bytes in files:
            try:
                result = await self._ingestion.ingest_file(
                    context=context,
                    knowledge_source=knowledge_source,
                    filename=filename,
                    raw_bytes=raw_bytes,
                    source_type="blob",
                    chunking_strategy=chunking_strategy,
                )
                chunks += result.chunk_count
                if result.status == "skipped":
                    skipped += 1
                else:
                    ingested += 1
            except Exception:  # noqa: BLE001 — one bad file must not sink the batch
                logger.error(
                    "Blob file ingestion failed; continuing",
                    extra={"filename": filename, "knowledge_source": knowledge_source},
                    exc_info=True,
                )
                skipped += 1

        logger.info(
            "Blob container ingested",
            extra={
                "knowledge_source": knowledge_source,
                "ingested": ingested,
                "skipped": skipped,
            },
        )
        return BlobIngestionResult(
            knowledge_source=knowledge_source,
            files_ingested=ingested,
            files_skipped=skipped,
            chunk_count=chunks,
        )

    def _list_files(
        self, container: str, prefix: str, connection_config: dict | None = None
    ) -> list[tuple[str, bytes]]:
        client = self._client(connection_config)
        try:
            blobs = client.list_blobs(container, folder_prefix=prefix or None)
            return [
                (blob_name.rsplit("/", 1)[-1] or blob_name,
                 client.read_blob_bytes(container_name, blob_name))
                for container_name, blob_name in blobs
            ]
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "Blob listing/download failed — check "
                "microsoft.azure.storage_account settings and the team's "
                "container/prefix.",
                code="blob_ingest_failed",
                cause=exc,
            ) from exc


_service: BlobIngestionService | None = None


def get_blob_ingestion_service() -> BlobIngestionService:
    global _service
    if _service is None:
        _service = BlobIngestionService()
    return _service
