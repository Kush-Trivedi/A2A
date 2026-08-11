from fastapi import APIRouter, Depends, status

from .....dto.common import ApiEnvelope
from .....dto.connections import (
    ConnectionHealthModel,
    ConnectionModel,
    RegisterConnectionRequest,
)
from .....entity.connections import TeamConnectionEntity
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.connections import ConnectionService
from ....dependencies import provide_connection_service

connections_v1_router = APIRouter(prefix="/connections", tags=["Connections"])

_CONNECTIONS_OBJ = "/api/v1/connections"


def _to_model(connection: TeamConnectionEntity) -> ConnectionModel:
    return ConnectionModel(
        id=connection.id,
        team_key=connection.team_key,
        name=connection.name,
        connection_type=connection.connection_type,
        description=connection.description,
        status=connection.status,
        config=dict(connection.config or {}),
        secret_keys=sorted((connection.secrets or {}).keys()),
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


@connections_v1_router.post(
    "",
    response_model=ApiEnvelope[ConnectionModel],
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_CONNECTIONS_OBJ, "POST")),
    ],
)
async def register_connection(
    body: RegisterConnectionRequest,
    context: SessionContext = Depends(get_current_context),
    service: ConnectionService = Depends(provide_connection_service),
) -> ApiEnvelope[ConnectionModel]:
    """Register or update a team-owned connection. Secret values are encrypted
    at rest and never echoed back — responses list secret KEY names only."""
    connection = await service.register_connection(
        context=context,
        team_key=body.team_key,
        name=body.name,
        connection_type=body.connection_type,
        description=body.description,
        config=body.config,
        secrets=body.secrets,
    )
    return ApiEnvelope(data=_to_model(connection), message="Connection registered.")


@connections_v1_router.get(
    "",
    response_model=ApiEnvelope[list[ConnectionModel]],
    dependencies=[Depends(require_permission(_CONNECTIONS_OBJ, "GET"))],
)
async def list_connections(
    team_key: str | None = None,
    context: SessionContext = Depends(get_current_context),
    service: ConnectionService = Depends(provide_connection_service),
) -> ApiEnvelope[list[ConnectionModel]]:
    connections = await service.list_connections(context=context, team_key=team_key)
    return ApiEnvelope(data=[_to_model(c) for c in connections])


@connections_v1_router.get(
    "/{name}/health",
    response_model=ApiEnvelope[ConnectionHealthModel],
    dependencies=[Depends(require_permission(_CONNECTIONS_OBJ, "GET"))],
)
async def connection_health(
    name: str,
    context: SessionContext = Depends(get_current_context),
    service: ConnectionService = Depends(provide_connection_service),
) -> ApiEnvelope[ConnectionHealthModel]:
    connection, health_status, detail = await service.health(context=context, name=name)
    return ApiEnvelope(
        data=ConnectionHealthModel(
            name=connection.name,
            connection_type=connection.connection_type,
            status=health_status,
            detail=detail,
        )
    )
