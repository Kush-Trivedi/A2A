from ..base import StrictBaseModel


class ContextEnvelopeModel(StrictBaseModel):
    """Who the agent is acting FOR. Tenant identity always comes from the
    service token server-side; roles here are re-enforced by Casbin."""

    user_id: str
    actor_id: str = ""
    roles: list[str] = []
    session_id: str | None = None
    correlation_id: str | None = None
    purpose: str = ""
    delegated_from: list[str] = []  # hop chain — bounded + cycle-checked at M5


class RetrieveRequest(StrictBaseModel):
    envelope: ContextEnvelopeModel
    agent_key: str
    query: str
    mode: str | None = None            # dense | sparse | hybrid; None = yaml default
    top_k: int | None = None
    min_similarity: float | None = None


class RetrievedChunkModel(StrictBaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    file_name: str
    source_uri: str
    content: str
    token_count: int
    score: float
    origin: str  # dense | sparse | hybrid | graph | neighbor


class RetrieveResponse(StrictBaseModel):
    query: str
    mode: str
    chunks: list[RetrievedChunkModel]


class ChatMessageModel(StrictBaseModel):
    role: str  # system | user | assistant
    content: str


class LlmStreamRequest(StrictBaseModel):
    envelope: ContextEnvelopeModel
    agent_key: str
    messages: list[ChatMessageModel]
    max_output_tokens: int | None = None


class CatalogRequest(StrictBaseModel):
    envelope: ContextEnvelopeModel
    agent_key: str


class FilesContextRequest(StrictBaseModel):
    """Session-scoped uploads for the envelope's session — the file
    agent's only knowledge source. The session id comes from the envelope
    (contextId), never as a free parameter."""

    envelope: ContextEnvelopeModel
    agent_key: str


class SessionDocumentModel(StrictBaseModel):
    file_name: str
    sha256: str
    characters: int
    content: str


class FilesContextResponse(StrictBaseModel):
    session_id: str
    documents: list[SessionDocumentModel]


class CatalogAgentModel(StrictBaseModel):
    agent_key: str
    display_name: str
    description: str
    team_key: str


class CatalogResponse(StrictBaseModel):
    agents: list[CatalogAgentModel]
