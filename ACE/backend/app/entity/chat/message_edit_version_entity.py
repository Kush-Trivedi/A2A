from sqlmodel import Field
from backend.app.entity.base_models import UUIDModel, CreatedAtModel
from sqlalchemy import Column, Text, Index, ForeignKey, Integer, UniqueConstraint

class MessageEditVersionEntity(UUIDModel, CreatedAtModel, table=True):
    __tablename__ = "message_edit_versions"
    __table_args__ = (
        UniqueConstraint("chain_id", "version_number", name="uq_edit_chain_version"),
        Index("idx_edit_versions_chain", "chain_id", "version_number"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    chain_id: str = Field(sa_column=Column(Text, ForeignKey("message_edit_chains.id"), nullable=False))
    version_number: int = Field(sa_column=Column(Integer, nullable=False))
    user_message: str  = Field(sa_column=Column(Text, nullable=False))
    assistant_message: str | None  = Field(default=None, sa_column=Column(Text, nullable=True))
