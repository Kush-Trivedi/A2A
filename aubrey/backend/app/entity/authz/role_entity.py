from sqlmodel import Field 
from backend.app.entity.base_models import TimestampModel
from sqlalchemy import Boolean, Column, Index, Text, UniqueConstraint

class RoleEntity(TimestampModel, table=True):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_role_tenant_key"),
        Index("idx_roles_tenant", "tenant_id"),
    )
    id: str = Field(
        sa_column=Column(Text, primary_key=True)
    )
    tenant_id: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    key: str = Field(
        sa_column=Column(Text, nullable=False),
        description="The unique key of the role within the tenant."
    )
    name: str = Field(
        sa_column=Column(Text, nullable=False),
        description="The name of the role."
    )
    description: str = Field(
        sa_column=Column(Text, nullable=True),
        description="A brief description of the role."
    )
    is_system: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
        description="Indicates whether the role is active."
    )