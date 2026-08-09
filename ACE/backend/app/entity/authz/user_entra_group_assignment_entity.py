from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field
from backend.app.entity.base_models import CreatedAtModel

class UserEntraGroupAssignmentEntity(CreatedAtModel, table=True):
    __tablename__ = "user_entra_group_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", 
            "user_id", 
            "group_id", 
            name="uq_user_entra_group"
        ),
        Index("idx_user_entra_group_user", "tenant_id", "user_id"),
        Index("idx_user_entra_group_group", "tenant_id", "group_id"),
    )
    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    group_id: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="entra_id_token", sa_column=Column(Text, nullable=False, default="entra_id_token"))