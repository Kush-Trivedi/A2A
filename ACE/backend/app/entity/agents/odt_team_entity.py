from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint
from backend.app.entity.base_models import TimestampModel


class OdtTeamEntity(TimestampModel, table=True):
    __tablename__ = "odt_teams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_odt_teams_tenant_key"),
        Index("idx_odt_teams_tenant", "tenant_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    key: str = Field(sa_column=Column(Text, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    contact_email: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
