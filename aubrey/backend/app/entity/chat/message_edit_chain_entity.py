from sqlalchemy import Column, ForeignKey, Index, Text, UniqueConstraint
from sqlmodel import Field

from backend.app.entity.base_models import TimeStampedModel

class MessageEditChainEntity(TimeStampedModel, table=True):
    __tablename__ = "message_edit_chains"
    __table_args__ = (
            UniqueConstraint("message_id", "created_by", name="uq_message_edit_chain_message"),
            Index("idx_message_edit_chain_session","session_id", "message_id"),
        )

    id: int = Field(default=None, primary_key=True)
    message_id: str = Field(
        sa_column=Column(Text, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    )
    session_id: str = Field(
        sa_column=Column(Text, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    )
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))

    