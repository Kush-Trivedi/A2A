from fastapi import APIRouter, Depends
from .....dto.common import ApiEnvelope
from .....security.session import SessionContext
from .....services.authz import AuthorizationService
from ....dependencies import provide_authorization_service
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf

from .....dto.authz import (
    AddPolicyRequest,
    DeleteRoleMappingRequest,
    MyPermissionsResponse,
    PolicyAuditEntry,
    PolicyTuple,
    RemovePolicyRequest,
    RoleMappingRequest,
    RoleMappingResponse,
)

admin_v1_router = APIRouter(prefix="/admin", tags=["Admin / Authorization"])


_ADMIN_OBJ = "/api/v1/admin/authz"


@admin_v1_router.get(
    "/policies",
    response_model=ApiEnvelope[list[PolicyTuple]],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def list_policies(
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[list[PolicyTuple]]:
    return ApiEnvelope(data=await service.list_policies())


@admin_v1_router.post(
    "/policies",
    response_model=ApiEnvelope[dict],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_ADMIN_OBJ, "POST")),
    ],
)
async def add_policy(
    body: AddPolicyRequest,
    context: SessionContext = Depends(get_current_context),
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[dict]:
    added = await service.add_policy(policy=PolicyTuple(**body.model_dump()), actor=context)
    return ApiEnvelope(data={"added": added})


@admin_v1_router.post(
    "/policies/remove",
    response_model=ApiEnvelope[dict],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_ADMIN_OBJ, "DELETE")),
    ],
)
async def remove_policy(
    body: RemovePolicyRequest,
    context: SessionContext = Depends(get_current_context),
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[dict]:
    removed = await service.remove_policy(policy=PolicyTuple(**body.model_dump()), actor=context)
    return ApiEnvelope(data={"removed": removed})


@admin_v1_router.post(
    "/policies/reload",
    response_model=ApiEnvelope[dict],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_ADMIN_OBJ, "POST")),
    ],
)
async def reload_policies(
    context: SessionContext = Depends(get_current_context),
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[dict]:
    return ApiEnvelope(data={"loaded": await service.reload_policies(actor=context)})


@admin_v1_router.get(
    "/policies/audit",
    response_model=ApiEnvelope[list[PolicyAuditEntry]],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def list_audit(
    limit: int = 100,
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[list[PolicyAuditEntry]]:
    return ApiEnvelope(data=await service.list_audit(limit=limit))


@admin_v1_router.get(
    "/role-mappings",
    response_model=ApiEnvelope[list[RoleMappingResponse]],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def list_role_mappings(
    tenant_id: str,
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[list[RoleMappingResponse]]:
    return ApiEnvelope(data=await service.list_role_mappings(tenant_id=tenant_id))


@admin_v1_router.post(
    "/role-mappings",
    response_model=ApiEnvelope[RoleMappingResponse],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_ADMIN_OBJ, "POST")),
    ],
)
async def upsert_role_mapping(
    body: RoleMappingRequest,
    context: SessionContext = Depends(get_current_context),
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[RoleMappingResponse]:
    return ApiEnvelope(data=await service.upsert_role_mapping(request=body, actor=context))


@admin_v1_router.post(
    "/role-mappings/delete",
    response_model=ApiEnvelope[dict],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_ADMIN_OBJ, "DELETE")),
    ],
)
async def delete_role_mapping(
    body: DeleteRoleMappingRequest,
    context: SessionContext = Depends(get_current_context),
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[dict]:
    deleted = await service.delete_role_mapping(mapping_id=body.id, actor=context)
    return ApiEnvelope(data={"deleted": deleted})


@admin_v1_router.get(
    "/me/permissions",
    response_model=ApiEnvelope[MyPermissionsResponse],
)
async def my_permissions(
    context: SessionContext = Depends(get_current_context),
    service: AuthorizationService = Depends(provide_authorization_service),
) -> ApiEnvelope[MyPermissionsResponse]:
    policies = await service.my_policies(context=context)
    return ApiEnvelope(
        data=MyPermissionsResponse(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            roles=list(context.roles),
            policies=policies,
        )
    )
