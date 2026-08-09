from sqlmodel import Field 
from backend.app.entity.base_models import CreatedAtModel
from sqlalchemy import Column, Index, Text, UniqueConstraint

class UserRoleAssignmentEntity(CreatedAtModel, table=True):
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id", 
            "role", 
            name="uq_user_role"
        ),
        Index("idx_user_role_tenant_scope", "tenant_id", "user_id"),
    )
    id: str = Field(sa_column=Column(Text, primary_key=True,))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    role: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(default="local", sa_column=Column(Text, nullable=False))