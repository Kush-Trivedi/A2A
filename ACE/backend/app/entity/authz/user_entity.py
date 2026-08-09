from sqlmodel import Field 
from datetime import datetime
from backend.app.entity.base_models import TimestampModel
from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint


class UserEntity(TimestampModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_subject_id", name="uq_users_subject"),
        Index("ix_users_tenant_status", "tenant_id", "status"),
        Index("idx_users_email", "email"),
    )
    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    external_subject_id: str = Field(sa_column=Column(Text, nullable=False))
    email: str = Field(sa_column=Column(Text, nullable=False))
    first_name: str = Field(sa_column=Column(Text, nullable=True))
    last_name: str = Field(sa_column=Column(Text, nullable=True))
    auth_provider: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="active", sa_column=Column(Text, nullable=False))
    last_login_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )