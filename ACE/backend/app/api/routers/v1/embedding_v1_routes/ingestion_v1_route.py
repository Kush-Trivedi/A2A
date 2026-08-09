from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from .....dto.common import ApiEnvelope
from .....dto.embedding import (
    IngestBlobRequest,
    IngestJobAcceptedResponse,
    IngestJobStatusResponse,
    IngestResponse,
    IngestSharePointRequest,
    IngestTextRequest,
)
from .....services.knowledge.ingestion_job_service import get_ingestion_job_service
from .....security.dependencies import require_csrf
from .....security.rate_limiter import get_rate_limiter
from .....security.session import SessionContext
from .....services.embedding.ingestion_service import IngestionResult, IngestionService
from .....services.knowledge.blob_ingestion_service import BlobIngestionService
from .....services.knowledge.sharepoint_ingestion_service import (
    SharePointIngestionService,
)
from .....utils.common.logger import Logger
from ....dependencies import (
    provide_blob_ingestion_service,
    provide_ingestion_service,
    provide_sharepoint_ingestion_service,
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
