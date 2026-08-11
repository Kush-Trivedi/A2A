from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel


CONNECTION_TYPES: tuple[str, ...] = ("sharepoint", "storage_blob", "databricks", "twilio")

CONNECTION_STATUS_ACTIVE = "active"
CONNECTION_STATUS_DISABLED = "disabled"


class TeamConnectionEntity(TimestampModel, table=True):
    """A team-owned integration configuration, referenced by NAME.

    Teams register their SharePoint site / storage account / Databricks
    workspace / Twilio number here once; agents and ingestion refer to the
    connection by name. Secret values are encrypted at rest (enc:: envelope);
    non-secret settings live in `config`. ACE yaml never holds any of this.
    """

    __tablename__ = "team_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_team_connections_tenant_name"),
        Index("idx_team_connections_tenant", "tenant_id"),
        Index("idx_team_connections_team", "tenant_id", "team_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    team_key: str = Field(sa_column=Column(Text, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    connection_type: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    status: str = Field(
        default=CONNECTION_STATUS_ACTIVE,
        sa_column=Column(Text, nullable=False, default=CONNECTION_STATUS_ACTIVE),
    )
    config: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    secrets: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
