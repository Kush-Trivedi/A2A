from sqlmodel import Field
from backend.app.entity.base_models import CreatedAtModel
from sqlalchemy import Column, Index, Text, UniqueConstraint

class UserOrgAssignmentEntity(CreatedAtModel, table=True):
    __tablename__ = "user_org_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id", 
            "org_unit_id", 
            name="uq_user_org_assignment"
        ),
        Index("idx_user_org_scope", "tenant_id", "user_id"),
    )
    id: str = Field(sa_column=Column(Text, primary_key=True,))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    org_unit_id: str = Field(sa_column=Column(Text, nullable=False))
    relationship: str = Field(sa_column=Column(Text, nullable=False))