from sqlmodel import Field
from backend.app.entity.base_models import TimestampModel
from sqlalchemy import Boolean, Column, Index, Text, UniqueConstraint


class EntraClaimType:
    APP_ROLE = "app_role"
    GROUP = "group"


class EntraRoleMappingEntity(TimestampModel, table=True):
    __tablename__ = "entra_role_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "claim_type",
            "claim_value",
            "role_key",
            name="uq_entra_role_mappings_claim_role",
        ),
        Index("idx_entra_role_mappings_lookup", "tenant_id", "claim_type", "claim_value"),
        Index("idx_entra_role_mappings_role", "tenant_id", "role_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    claim_type: str = Field(
        sa_column=Column(Text, nullable=False),
        description="'app_role' or 'group' (see EntraClaimType).",
    )
    claim_value: str = Field(
        sa_column=Column(Text, nullable=False),
        description="App role value (for app_role) or AD group object id GUID (for group).",
    )
    role_key: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Internal role key granted by this mapping; references roles.key (app-enforced).",
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
        description="Allows disabling a mapping without deleting it.",
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Optional admin note, e.g. the human-readable AD group name.",
    )
