from typing import Any
from pydantic import Field 
from datetime import datetime
from ..base import StrictBaseModel

class SendChatRequest(StrictBaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    agent: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)

class ChatSourceModel(StrictBaseModel):
    document_id: str
    source_name: str
    knowledge_source: str
    score: float

class ChatTurnResponse(StrictBaseModel):
    session_id: str
    message_id: str
    agent_id: str
    answer: str
    sources: list[ChatSourceModel] = Field(default_factory=list)
    refusal: dict[str, Any] | None = Field(
        default=None,
        description="Present when access was denied: type, message, owning team, contact.",
    )

class CreateSessionRequest(StrictBaseModel):
    title: str = Field(default="New Chat", max_length=120)

class RenameSessionRequest(StrictBaseModel):
    title: str = Field(default="New Chat", max_length=120)

class EditMessageRequest(StrictBaseModel):
    content: str = Field(..., min_length=1, max_length=8000)

class MessageFeedbackRequest(StrictBaseModel):
    feedback: str = Field(default=None, min_length=1, max_length=32)

class MessageFeedbackResponse(StrictBaseModel):
    message_id: str
    feedback: str | None = None

class SessionSummaryModel(StrictBaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

class ChatMessageModel(StrictBaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    feedback: str | None = None
    edited: bool = False

class SessionMessagesResponse(StrictBaseModel):
    session_id: str
    messages: list[ChatMessageModel] = Field(default_factory=list)

class AgentSummaryModel(StrictBaseModel):
    id: str
    display_name: str
    description: str = ""