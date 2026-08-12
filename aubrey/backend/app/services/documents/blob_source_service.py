"""Blob storage source: a whole container/prefix (folders inside folders —
listing is recursive), one named file, or one blob URL (the Event Grid
case). Everything downloads per file and flows through the shared
DocumentPipeline. The account is platform-held (microsoft.azure.
storage_account in the env yaml)."""

import asyncio

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...security.session import SessionContext
from ...utils.azure.azure_helpers import AzureStorageBlobClient
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from .document_pipeline import (
    DocumentPipeline,
    DocumentSink,
    PipelineResult,
    SourceFile,
    get_document_pipeline,
)

logger = Logger(__name__).get_logger()


class BlobSourceService:
    def __init__(self, pipeline: DocumentPipeline | None = None) -> None:
        self._pipeline = pipeline or get_document_pipeline()

    def _client(self) -> AzureStorageBlobClient:
        context = get_application_context()
        storage = context.microsoft.get("azure", {}).get("storage_account", {}) or {}
        account_url = storage.get("account_url")
        if not PlaceholderPolicy.is_configured(account_url):
            raise ValidationError(
                "Blob storage is not configured. Set "
                "microsoft.azure.storage_account.account_url in the env yaml."
            )
        return AzureStorageBlobClient(
            account_url=str(account_url),
            managed_identity_client_id=context.managed_identity_client_id or None,
        )

    async def ingest(
        self,
        *,
        context: SessionContext,
        team_key: str,
        agent_key: str,
        container: str = "",
        prefix: str = "",
        file_name: str | None = None,
        blob_url: str | None = None,
        sink: DocumentSink | None = None,
    ) -> PipelineResult:
        client = self._client()

        if blob_url and blob_url.strip():
            resolved_container, blob_name = client.parse_blob_url(blob_url.strip())
            files = [self._to_source_file(client, resolved_container, blob_name)]
            container = resolved_container
        else:
            if not container.strip():
                raise ValidationError("Provide either a container or a blob_url.")
            files = await asyncio.to_thread(
                self._list_files, client, container, prefix
            )
            if file_name and file_name.strip():
                wanted = file_name.strip().lower()
                files = [
                    f for f in files if f.name.rsplit("/", 1)[-1].lower() == wanted
                ]
                if not files:
                    raise ValidationError(
                        f"Blob '{file_name}' was not found in container "
                        f"'{container}' with the given prefix.",
                        details={"file_name": file_name, "container": container},
                    )

        return await self._pipeline.run(
            tenant_id=context.tenant_id,
            team_key=team_key.strip().lower(),
            agent_key=agent_key.strip().lower(),
            source_type="blob",
            batch_name=f"blob:{container}/{prefix or ''}".rstrip("/"),
            files=files,
            download=self._downloader(client),
            properties={
                "container": container,
                "prefix": prefix,
                "file_name": file_name or "",
                "blob_url": blob_url or "",
            },
            sink=sink,
        )

    def _list_files(
        self, client: AzureStorageBlobClient, container: str, prefix: str
    ) -> list[SourceFile]:
        try:
            return [
                self._to_source_file(client, container, blob_name)
                for blob_name in client.list_blobs(container, prefix or None)
            ]
        except ValidationError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                "Blob listing failed — check the storage account and container/prefix.",
                code="blob_listing_failed",
                cause=exc,
            ) from exc

    @staticmethod
    def _to_source_file(
        client: AzureStorageBlobClient, container: str, blob_name: str
    ) -> SourceFile:
        return SourceFile(
            name=blob_name,
            uri=f"{client.account_url}/{container}/{blob_name}",
            extra={"container": container, "blob_name": blob_name},
        )

    def _downloader(self, client: AzureStorageBlobClient):
        async def download(source_file: SourceFile) -> bytes:
            return await asyncio.to_thread(
                client.read_blob_bytes,
                source_file.extra["container"],
                source_file.extra["blob_name"],
            )

        return download


_service: BlobSourceService | None = None


def get_blob_source_service() -> BlobSourceService:
    global _service
    if _service is None:
        _service = BlobSourceService()
    return _service
