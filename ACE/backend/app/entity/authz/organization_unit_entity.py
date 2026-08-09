from sqlmodel import Field
from backend.app.entity.base_models import CreatedAtModel
from sqlalchemy import Column, Index, Text, UniqueConstraint

class OrganizationUnitEntity(CreatedAtModel, table=True):
    __tablename__ = "organization_units"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_org_unit_code"),
        Index("ix_org_units_parent", "tenant_id", "parent_id"),
    )

    id: int = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    code: str = Field(sa_column=Column(Text, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    type: str = Field(sa_column=Column(Text, nullable=False))
    parent_id: str | None = Field(sa_column=Column(Text, nullable=True))
    