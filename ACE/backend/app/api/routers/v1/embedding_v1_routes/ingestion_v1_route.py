from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from .....dto.common import ApiEnvelope
from .....dto.embedding import (
    IngestBlobRequest,
    IngestJobAcceptedResponse,
    IngestJobStatusResponse,
    IngestResponse,
    IngestSharePointRequest,
    IngestSourceRequest,
    IngestTextRequest,
    KnowledgeSourceModel,
)
from .....entity.knowledge import KnowledgeSourceEntity
from .....services.connections import ConnectionService
from .....services.knowledge.ingestion_job_service import get_ingestion_job_service
from .....services.knowledge.source_registry_service import SourceRegistryService
from .....security.dependencies import require_csrf
from .....security.rate_limiter import get_rate_limiter
from .....security.session import SessionContext
from .....services.embedding.ingestion_service import IngestionResult, IngestionService
from .....services.knowledge.blob_ingestion_service import BlobIngestionService
from .....services.knowledge.sharepoint_ingestion_service import (
    SharePointIngestionService,
)
from .....utils.common.logger import Logger
from .....utils.errors import ValidationError
from ....dependencies import (
    provide_blob_ingestion_service,
    provide_connection_service,
    provide_ingestion_service,
    provide_sharepoint_ingestion_service,
    provide_source_registry_service,
)

logger = Logger(__name__).get_logger()

ingestion_v1_router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


def _to_ingest_response(result: IngestionResult) -> IngestResponse:
    return IngestResponse(
        document_id=result.document_id,
        source_name=result.source_name,
        knowledge_source=result.knowledge_source,
        chunk_count=result.chunk_count,
        status=result.status,
    )


@ingestion_v1_router.post(
    "/ingest",
    response_model=ApiEnvelope[IngestResponse],
    status_code=status.HTTP_201_CREATED,
)
async def ingest_text(
    payload: IngestTextRequest,
    context: SessionContext = Depends(require_csrf),
    service: IngestionService = Depends(provide_ingestion_service),
) -> ApiEnvelope[IngestResponse]:
    await get_rate_limiter().check(context.actor_id)
    result = await service.ingest_text(
        context=context,
        knowledge_source=payload.knowledge_source,
        title=payload.source_name,
        text=payload.text,
        session_id=payload.session_id,
        source_type=payload.source_type or "upload",
        chunking_strategy=payload.chunking_strategy,
    )
    return ApiEnvelope(data=_to_ingest_response(result), message="Ingested.")


@ingestion_v1_router.post(
    "/ingest/sharepoint",
    response_model=ApiEnvelope[IngestJobAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_sharepoint(
    payload: IngestSharePointRequest,
    context: SessionContext = Depends(require_csrf),
    service: SharePointIngestionService = Depends(provide_sharepoint_ingestion_service),
) -> ApiEnvelope[IngestJobAcceptedResponse]:
    await get_rate_limiter().check(context.actor_id)

    async def _work() -> dict:
        result = await service.ingest_folder(
            context=context,
            source_name=payload.source_name,
            site_path=payload.site_path,
            drive_name=payload.drive_name,
            folder_path=payload.folder_path,
            chunking_strategy=payload.chunking_strategy,
        )
        return {
            "knowledge_source": result.knowledge_source,
            "files_ingested": result.files_ingested,
            "files_skipped": result.files_skipped,
            "chunk_count": result.chunk_count,
        }

    job_id = await get_ingestion_job_service().start(
        context=context, kind="sharepoint", source_name=payload.source_name, work=_work
    )
    return ApiEnvelope(
        data=IngestJobAcceptedResponse(job_id=job_id),
        message="SharePoint ingestion started.",
    )


@ingestion_v1_router.get(
    "/ingest/jobs/{job_id}",
    response_model=ApiEnvelope[IngestJobStatusResponse],
)
async def get_ingest_job(
    job_id: str,
    context: SessionContext = Depends(require_csrf),
) -> ApiEnvelope[IngestJobStatusResponse]:
    job = await get_ingestion_job_service().get(context=context, job_id=job_id)
    return ApiEnvelope(
        data=IngestJobStatusResponse(
            job_id=job.id,
            kind=job.kind,
            source_name=job.source_name,
            status=job.status,
            detail=dict(job.detail or {}),
        )
    )


@ingestion_v1_router.post(
    "/ingest/blob",
    response_model=ApiEnvelope[IngestJobAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_blob(
    payload: IngestBlobRequest,
    context: SessionContext = Depends(require_csrf),
    service: BlobIngestionService = Depends(provide_blob_ingestion_service),
) -> ApiEnvelope[IngestJobAcceptedResponse]:
    await get_rate_limiter().check(context.actor_id)

    async def _work() -> dict:
        result = await service.ingest_container(
            context=context,
            source_name=payload.source_name,
            container=payload.container,
            prefix=payload.prefix,
            chunking_strategy=payload.chunking_strategy,
        )
        return {
            "knowledge_source": result.knowledge_source,
            "files_ingested": result.files_ingested,
            "files_skipped": result.files_skipped,
            "chunk_count": result.chunk_count,
        }

    job_id = await get_ingestion_job_service().start(
        context=context, kind="blob", source_name=payload.source_name, work=_work
    )
    return ApiEnvelope(
        data=IngestJobAcceptedResponse(job_id=job_id),
        message="Blob ingestion started.",
    )


def _to_source_model(source: KnowledgeSourceEntity) -> KnowledgeSourceModel:
    return KnowledgeSourceModel(
        source_name=source.source_name,
        owner_team_key=source.owner_team_key,
        connection_name=source.connection_name,
        description=source.description,
        status=source.status,
        location=dict(source.location or {}),
        chunking=dict(source.chunking or {}),
        embedding=dict(source.embedding or {}),
        agents=list(source.agents or []),
        roles=list(source.roles or []),
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@ingestion_v1_router.post(
    "/ingest/source",
    response_model=ApiEnvelope[IngestJobAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_source(
    payload: IngestSourceRequest,
    context: SessionContext = Depends(require_csrf),
    sharepoint: SharePointIngestionService = Depends(provide_sharepoint_ingestion_service),
    blob: BlobIngestionService = Depends(provide_blob_ingestion_service),
    connections: ConnectionService = Depends(provide_connection_service),
    source_registry: SourceRegistryService = Depends(provide_source_registry_service),
) -> ApiEnvelope[IngestJobAcceptedResponse]:
    """The ONE parameterized ingestion entry point: the team names their
    connection, location, chunking, embedding, and access (bound agents +
    reader roles). ACE provides the pipeline; nothing team-specific lives in
    ACE config."""
    await get_rate_limiter().check(context.actor_id)

    connection = await connections.resolve_config(
        tenant_id=context.tenant_id, name=payload.connection
    )
    connection_type = str(connection.get("connection_type") or "")
    if connection_type not in ("sharepoint", "storage_blob"):
        raise ValidationError(
            f"Connection type '{connection_type}' is not an ingestion source. "
            "Databricks data is queried live via the genie capability, not ingested.",
            details={"connection": payload.connection},
        )
    if connection.get("team_key") != payload.team_key.strip().lower():
        raise ValidationError(
            "Connection is owned by another team.",
            details={"connection": payload.connection},
        )

    prefix = "sharepoint" if connection_type == "sharepoint" else "blob"
    bare_name = payload.source_name.strip().lower().split(":", 1)[-1]
    full_source_name = f"{prefix}:{bare_name}"

    source, policies_seeded = await source_registry.register_source(
        context=context,
        source_name=full_source_name,
        owner_team_key=payload.team_key,
        connection_name=payload.connection,
        description=payload.description,
        location=dict(payload.location),
        chunking=payload.chunking.model_dump(),
        embedding=payload.embedding.model_dump(),
        agents=list(payload.access.agents),
        roles=list(payload.access.roles),
    )

    location = dict(payload.location)
    chunking_strategy = payload.chunking.strategy

    async def _work() -> dict:
        if connection_type == "sharepoint":
            result = await sharepoint.ingest_folder(
                context=context,
                source_name=bare_name,
                site_path=str(location.get("site_path", "")),
                drive_name=str(location.get("drive_name", "")),
                folder_path=str(location.get("folder_path", "")),
                chunking_strategy=chunking_strategy,
                connection_config=connection,
            )
        else:
            result = await blob.ingest_container(
                context=context,
                source_name=bare_name,
                container=str(location.get("container", "")),
                prefix=str(location.get("prefix", "")),
                chunking_strategy=chunking_strategy,
                connection_config=connection,
            )
        return {
            "knowledge_source": result.knowledge_source,
            "files_ingested": result.files_ingested,
            "files_skipped": result.files_skipped,
            "chunk_count": result.chunk_count,
            "policies_seeded": policies_seeded,
        }

    job_id = await get_ingestion_job_service().start(
        context=context,
        kind=connection_type,
        source_name=source.source_name,
        work=_work,
    )
    return ApiEnvelope(
        data=IngestJobAcceptedResponse(job_id=job_id),
        message="Ingestion started; source registered with agent bindings and reader roles.",
    )


@ingestion_v1_router.get(
    "/sources",
    response_model=ApiEnvelope[list[KnowledgeSourceModel]],
)
async def list_sources(
    team_key: str | None = None,
    context: SessionContext = Depends(require_csrf),
    source_registry: SourceRegistryService = Depends(provide_source_registry_service),
) -> ApiEnvelope[list[KnowledgeSourceModel]]:
    sources = await source_registry.list_sources(context=context, team_key=team_key)
    return ApiEnvelope(data=[_to_source_model(s) for s in sources])


@ingestion_v1_router.post(
    "/ingest/file",
    response_model=ApiEnvelope[IngestResponse],
    status_code=status.HTTP_201_CREATED,
)
async def ingest_file(
    knowledge_source: str = Form(..., min_length=1, max_length=120),
    file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    source_type: str = Form(default="upload"),
    chunking_strategy: str | None = Form(default=None, max_length=40),
    context: SessionContext = Depends(require_csrf),
    service: IngestionService = Depends(provide_ingestion_service),
) -> ApiEnvelope[IngestResponse]:
    await get_rate_limiter().check(context.actor_id)
    raw_bytes = await file.read()
    result = await service.ingest_file(
        context=context,
        knowledge_source=knowledge_source,
        filename=file.filename or "upload",
        raw_bytes=raw_bytes,
        session_id=session_id,
        source_type=source_type,
        chunking_strategy=chunking_strategy,
    )
    return ApiEnvelope(data=_to_ingest_response(result), message="Ingested.")
