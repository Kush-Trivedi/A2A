from sqlmodel import Field
from sqlalchemy import Column, Text, Index, ForeignKey
from backend.app.entity.base_models import UUIDModel, CreatedAtModel

class MessageEditChainEntity(UUIDModel, CreatedAtModel, table=True):
    __tablename__ = "message_edit_chains"
    __table_args__ = (Index("idx_edit_chains_session", "session_id"),)

    id: str = Field(sa_column=Column(Text, primary_key=True))
    session_id: str = Field(sa_column=Column(Text, ForeignKey("chat_sessions.id"), nullable=False))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    anchor_position: str = Field(sa_column=Column(Text, nullable=False))
