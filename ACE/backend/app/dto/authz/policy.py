from pydantic import Field 
from datetime import datetime
from typing import Literal
from ..base import StrictBaseModel

class PolicyTuple(StrictBaseModel):
    role: str = Field(..., min_length=1, description="Internal role key (Casbin subject)")
    domain: str = Field("*", description=" Tenant id, or '*' for all tenants (Casbin domain)")
    resource: str = Field(..., min_length=1, description="Object/resource, supports keyMatch2 patterns")
    action: str = Field(..., min_length=1, description="Action, or '*' for any")
    effect: Literal["allow", "deny"] = Field("allow", description="Effect of the policy, either 'allow' or 'deny' PBAC Override")
    attrs: str = Field(
        "*", 
        description=(
            "Attribute selector path pattern (ABAC) matched with keyMatch2"
            "Use '*' for all callers, or patterns like "
            "'/tenant/*/user/*/attr/department/cardiology/*'."
        )
    )

class AddPolicyRequest(PolicyTuple):
    pass

class RemovePolicyRequest(PolicyTuple):
    pass

class PolicyAuditEntry(StrictBaseModel):
    created_at: datetime
    actor_id: str
    tenant_id: str
    action: str
    target_role: str | None = None
    target_domain: str | None = None
    target_resource: str | None = None
    target_action: str | None = None

class RoleMappingRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=1)
    claim_type: str = Field(..., min_length=1)
    claim_value: str = Field(..., min_length=1)
    role_key: str = Field(..., min_length=1)
    enabled: bool = Field(default=True)
    description: str | None = None

class RoleMappingResponse(StrictBaseModel):
    id: str
    tenant_id: str
    claim_type: str
    claim_value: str
    role_key: str
    enabled: bool
    description: str | None = None

class DeleteRoleMappingRequest(StrictBaseModel):
    id: str = Field(..., min_length=1)

class MyPermissionsResponse(StrictBaseModel):
    tenant_id: str
    user_id: str
    roles: list[str] = Field(default_factory=list)
    policies: list[PolicyTuple] = Field(default_factory=list)