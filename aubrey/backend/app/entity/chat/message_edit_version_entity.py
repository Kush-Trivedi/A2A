from sqlalchemy import Column, ForeignKey, Index, Text, UniqueConstraint
from sqlmodel import Field

from backend.app.entity.base_models import TimeStampedModel

class MessageEditVersionEntity(TimeStampedModel, table=True):
    __tablename__ = "message_edit_versions"
    __table_args__ = (
            UniqueConstraint("chain_id", "version_number", "created_by", name="uq_message_edit_chain_version"),
            Index("idx_message_edit_versions_chain","chain_id", "version_number"),
        )

    id: int = Field(default=None, primary_key=True)
    chain_id: str = Field(
        sa_column=Column(Text, ForeignKey("message_edit_chains.id", ondelete="CASCADE"), nullable=False)
    )
    version_number: int = Field(sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))