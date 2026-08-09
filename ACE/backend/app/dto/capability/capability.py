from typing import Any
from pydantic import Field
from ..base import StrictBaseModel


class CapabilityEnvelopeModel(StrictBaseModel):
    tenant_id: str = Field(..., min_length=1)
    actor_id: str = Field(..., min_length=1)
    user_id: str = ""
    roles: list[str] = Field(default_factory=list)
    correlation_id: str = ""
    chat_session_id: str = ""
    purpose: str = "chat"
    delegated_from: str = ""
    delegation_reason: str = ""


class CapabilityRetrieveRequest(StrictBaseModel):
    envelope: CapabilityEnvelopeModel
    query: str = Field(..., min_length=1)
    knowledge_sources: list[str] = Field(default_factory=list)
    session_id: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    retrieval_mode: str | None = None


class CapabilityChunkModel(StrictBaseModel):
    chunk_id: str
    document_id: str
    source_name: str
    knowledge_source: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityRetrieveResponse(StrictBaseModel):
    chunks: list[CapabilityChunkModel] = Field(default_factory=list)


class CapabilityLlmMessageModel(StrictBaseModel):
    role: str = Field(..., pattern=r"^(system|user|assistant)$")
    content: str = Field(..., min_length=1)


class CapabilityLlmChatRequest(StrictBaseModel):
    envelope: CapabilityEnvelopeModel
    agent_key: str = Field(..., min_length=1, max_length=60)
    deployment: str = Field(..., min_length=1, max_length=120)
    messages: list[CapabilityLlmMessageModel] = Field(..., min_length=1, max_length=100)


class CapabilityLlmChatResponse(StrictBaseModel):
    text: str
    deployment: str


class CapabilitySmsSendRequest(StrictBaseModel):
    envelope: CapabilityEnvelopeModel
    agent_key: str = Field(..., min_length=1, max_length=60)
    to_number: str = Field(..., min_length=5, max_length=20)
    body: str = Field(..., min_length=1, max_length=1600)


class CapabilitySmsSendResponse(StrictBaseModel):
    message_sid: str


class CapabilityCatalogRequest(StrictBaseModel):
    envelope: CapabilityEnvelopeModel


class CapabilityAgentModel(StrictBaseModel):
    id: str
    display_name: str
    description: str = ""
    team_key: str = ""
    is_remote: bool = False


class CapabilityCatalogResponse(StrictBaseModel):
    agents: list[CapabilityAgentModel] = Field(default_factory=list)
