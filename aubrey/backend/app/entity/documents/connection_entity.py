from typing import Any

from sqlalchemy import Column, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class ConnectionType:
    BLOB = "blob"
    SHAREPOINT = "sharepoint"


class ConnectionEntity(TimestampModel, table=True):
    """A team-owned data-source location. Credentials never live here — the
    platform identity is GRANTED access to the team's resource; this row only
    records where the data is (account/container or site/drive)."""

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "team_key", "connection_key",
            name="uq_connections_tenant_team_key",
        ),
        Index("idx_connections_tenant_team", "tenant_id", "team_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    team_key: str = Field(sa_column=Column(Text, nullable=False))
    connection_key: str = Field(sa_column=Column(Text, nullable=False))
    source_type: str = Field(sa_column=Column(Text, nullable=False))  # blob | sharepoint
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    # blob: {account_url, container} — sharepoint: {hostname, site_path, drive_name}
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
