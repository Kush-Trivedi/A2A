from datetime import datetime
from typing import Any

from ..base import StrictBaseModel


class CreateSessionRequest(StrictBaseModel):
    title: str = ""


class ChatTurnRequest(StrictBaseModel):
    question: str
    session_id: str | None = None  # omit to start a new conversation
    agent_key: str | None = None   # pin a specific agent; else the router decides


class ChatSessionModel(StrictBaseModel):
    id: str
    title: str
    channel: str
    created_at: datetime
    updated_at: datetime


class ChatMessageModel(StrictBaseModel):
    id: str
    session_id: str
    role: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime
