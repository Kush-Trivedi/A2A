from sqlmodel import Field 
from sqlalchemy import Column, Index, Text
from backend.app.entity.base_models import CreatedAtModel, IDModel, UUIDModel

class PolicyAuditLogEntity(IDModel, UUIDModel, CreatedAtModel, table=True):
    __tablename__ = "policy_audit_log"
    __table_args__ = (
        Index("idx_policy_audit_actor", "actor_id", "created_at"),
        Index("idx_policy_audit_target", "target_role", "created_at"),
        Index("idx_policy_audit_tenant", "tenant_id", "created_at")
    )

    actor_id: str = Field(
        sa_column=Column(Text, nullable=False),
        description="The ID of the actor who performed the action."
    )
    tenant_id: str = Field(
        sa_column=Column(Text, nullable=False),
        description="The ID of the tenant associated with the action."
    )
    action: str = Field(
        sa_column=Column(Text, nullable=False),
        description="The action performed by the actor."
    )
    target_role: str = Field(
        default=None,
        sa_column=Column(Text, nullable=False)
    )
    target_domain: str = Field(
        default=None,
        sa_column=Column(Text, nullable=False)
    )
    target_resource: str = Field(
        default=None,
        sa_column=Column(Text, nullable=False)
    )
    target_action: str = Field(
        default=None,
        sa_column=Column(Text, nullable=False)
    )   