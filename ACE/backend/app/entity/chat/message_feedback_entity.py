from sqlmodel import Field
from backend.app.entity.base_models import IDModel, UUIDModel, TimestampModel
from sqlalchemy import Column, Text, Index, ForeignKey, UniqueConstraint


class MessageFeedbackEntity(IDModel, UUIDModel, TimestampModel, table=True):
    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_message_feedback_message_id"),
        Index("idx_message_feedbacks_session", "session_id", "message_id"),
    )
    message_id: str = Field(sa_column=Column(Text, ForeignKey("chat_messages.id"), nullable=False))
    session_id: str = Field(sa_column=Column(Text, ForeignKey("chat_sessions.id"), nullable=False))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    feedback: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(default="chat_ui", sa_column=Column(Text, nullable=False))
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))