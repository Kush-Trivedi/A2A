"""Connection registry + document ingestion endpoints.

Connections are team-owned locations (register once); ingestion runs read
through them: team + agent + connection_key + a path within the connection.
Same session auth as the rest of the admin surface (Authorize in Swagger
with the CSRF token from /auth/me).
"""

from fastapi import APIRouter, Depends, status

from .....dto.base import ApiEnvelope
from .....dto.documents import (
    BlobIngestRequest,
    ConnectionModel,
    IngestResultModel,
    RegisterConnectionRequest,
    SharePointIngestRequest,
)
from .....entity.documents import ConnectionEntity
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.documents import (
    BlobSourceService,
    ConnectionService,
    PipelineResult,
    SharePointSourceService,
)
from .....services.knowledge import KnowledgeSinkFactory
from ....dependencies import (
    provide_blob_source_service,
    provide_connection_service,
    provide_knowledge_sink_factory,
    provide_sharepoint_source_service,
)

connections_router = APIRouter(prefix="/admin", tags=["Connections"])
documents_router = APIRouter(prefix="/documents", tags=["Documents"])

_ADMIN_OBJ = "/api/v1/admin"
_DOCUMENTS_OBJ = "/api/v1/documents"


def _to_connection(connection: ConnectionEntity) -> ConnectionModel:
    return ConnectionModel(
        id=connection.id,
        team_key=connection.team_key,
        connection_key=connection.connection_key,
        source_type=connection.source_type,
        description=connection.description,
        config={
            k: ("***" if any(s in k.lower() for s in ("secret", "token", "password", "key", "credential", "auth_header_value")) else str(v))
            for k, v in (connection.config or {}).items()
        },
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _to_result(result: PipelineResult) -> IngestResultModel:
    return IngestResultModel(
        batch_id=result.batch_id,
        processed=result.processed,
        linked=result.linked,
        skipped=result.skipped,
        failed=result.failed,
    )


@connections_router.post(
    "/connections",
    response_model=ApiEnvelope[ConnectionModel],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_ADMIN_OBJ, "POST"))],
)
async def register_connection(
    body: RegisterConnectionRequest,
    context: SessionContext = Depends(get_current_context),
    service: ConnectionService = Depends(provide_connection_service),
) -> ApiEnvelope[ConnectionModel]:
    connection = await service.register(
        context=context,
        team_key=body.team_key,
        connection_key=body.connection_key,
        source_type=body.source_type,
        config=body.config,
        description=body.description,
    )
    return ApiEnvelope(data=_to_connection(connection), message="Connection registered.")


@connections_router.get(
    "/connections",
    response_model=ApiEnvelope[list[ConnectionModel]],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def list_connections(
    team_key: str | None = None,
    context: SessionContext = Depends(get_current_context),
    service: ConnectionService = Depends(provide_connection_service),
) -> ApiEnvelope[list[ConnectionModel]]:
    connections = await service.list(context=context, team_key=team_key)
    return ApiEnvelope(data=[_to_connection(c) for c in connections])


@documents_router.post(
    "/ingest/blob",
    response_model=ApiEnvelope[IngestResultModel],
    dependencies=[Depends(require_csrf), Depends(require_permission(_DOCUMENTS_OBJ, "POST"))],
)
async def ingest_blob(
    body: BlobIngestRequest,
    context: SessionContext = Depends(get_current_context),
    service: BlobSourceService = Depends(provide_blob_source_service),
    sinks: KnowledgeSinkFactory = Depends(provide_knowledge_sink_factory),
) -> ApiEnvelope[IngestResultModel]:
    result = await service.ingest(
        context=context,
        team_key=body.team_key,
        agent_key=body.agent_key,
        connection_key=body.connection_key,
        prefix=body.prefix,
        file_name=body.file_name,
        blob_url=body.blob_url,
        sink=sinks.make_sink(
            tenant_id=context.tenant_id,
            chunking_strategy=body.chunking_strategy,
            build_graph=body.build_graph,
        ),
    )
    return ApiEnvelope(data=_to_result(result), message="Ingestion batch finished.")


@documents_router.post(
    "/ingest/sharepoint",
    response_model=ApiEnvelope[IngestResultModel],
    dependencies=[Depends(require_csrf), Depends(require_permission(_DOCUMENTS_OBJ, "POST"))],
)
async def ingest_sharepoint(
    body: SharePointIngestRequest,
    context: SessionContext = Depends(get_current_context),
    service: SharePointSourceService = Depends(provide_sharepoint_source_service),
    sinks: KnowledgeSinkFactory = Depends(provide_knowledge_sink_factory),
) -> ApiEnvelope[IngestResultModel]:
    result = await service.ingest(
        context=context,
        team_key=body.team_key,
        agent_key=body.agent_key,
        connection_key=body.connection_key,
        folder_path=body.folder_path,
        file_name=body.file_name,
        sink=sinks.make_sink(
            tenant_id=context.tenant_id,
            chunking_strategy=body.chunking_strategy,
            build_graph=body.build_graph,
        ),
    )
    return ApiEnvelope(data=_to_result(result), message="Ingestion batch finished.")
