"""Blob storage source. WHERE to read comes from the team's registered
connection (account_url + container); a whole prefix (folders inside folders
— listing is recursive), one named file, or one blob URL (the Event Grid
case). Access is the platform identity, granted RBAC on the team's account.
Everything downloads per file and flows through the shared DocumentPipeline."""

import asyncio

from ...config.application_context import get_application_context
from ...security.session import SessionContext
from ...utils.azure.azure_helpers import AzureStorageBlobClient
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from .connection_service import ConnectionService, get_connection_service
from .document_pipeline import (
    DocumentPipeline,
    DocumentSink,
    PipelineResult,
    SourceFile,
    get_document_pipeline,
)

logger = Logger(__name__).get_logger()


class BlobSourceService:
    def __init__(
        self,
        pipeline: DocumentPipeline | None = None,
        connections: ConnectionService | None = None,
    ) -> None:
        self._pipeline = pipeline or get_document_pipeline()
        self._connections = connections or get_connection_service()

    @staticmethod
    def _client(account_url: str) -> AzureStorageBlobClient:
        context = get_application_context()
        return AzureStorageBlobClient(
            account_url=account_url,
            managed_identity_client_id=context.managed_identity_client_id or None,
        )

    async def ingest(
        self,
        *,
        context: SessionContext,
        team_key: str,
        agent_key: str,
        connection_key: str,
        prefix: str = "",
        file_name: str | None = None,
        blob_url: str | None = None,
        sink: DocumentSink | None = None,
    ) -> PipelineResult:
        connection = await self._connections.get(
            context=context, team_key=team_key, connection_key=connection_key
        )
        if connection.source_type != "blob":
            raise ValidationError(
                f"Connection '{connection.connection_key}' is "
                f"'{connection.source_type}', not blob storage.",
            )
        client = self._client(connection.config["account_url"])
        container = connection.config["container"]

        if blob_url and blob_url.strip():
            # parse_blob_url refuses URLs from any other storage account.
            url_container, blob_name = client.parse_blob_url(blob_url.strip())
            if url_container != container:
                raise ValidationError(
                    "The blob URL points at a different container than the connection.",
                    details={"url_container": url_container, "connection": container},
                )
            files = [self._to_source_file(client, container, blob_name)]
        else:
            files = await asyncio.to_thread(self._list_files, client, container, prefix)
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
            team_key=connection.team_key,
            agent_key=agent_key.strip().lower(),
            source_type="blob",
            batch_name=f"blob:{connection.connection_key}/{prefix or ''}".rstrip("/"),
            files=files,
            download=self._downloader(client),
            properties={
                "connection_key": connection.connection_key,
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
