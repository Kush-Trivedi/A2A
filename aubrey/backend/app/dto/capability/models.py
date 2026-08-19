from typing import Any

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
    delegated_from: list[str] = []  # hop chain — bounded + cycle-checked (M5)
    # M10-S3 platform signature over the identity fields (incl. the chain).
    # Agents carry these VERBATIM from the inbound envelope; when signing is
    # enabled server-side, missing/invalid/stale -> 403.
    sig: str = ""
    issued_at: str = ""


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


class DataGenieRequest(StrictBaseModel):
    """Natural-language question against a team-owned Genie connection.
    Conversation continuity is platform-managed per chat session — agents
    never track Genie conversation ids."""

    envelope: ContextEnvelopeModel
    agent_key: str
    connection_key: str
    question: str


class DataSqlRequest(StrictBaseModel):
    """Direct SQL on a team-owned warehouse connection — the fast lane for
    known lookups (dashboard row fetch, cached statements)."""

    envelope: ContextEnvelopeModel
    agent_key: str
    connection_key: str
    statement: str


class DataAskRequest(StrictBaseModel):
    """Fast text-to-SQL lane: our LLM writes the SQL from the introspected
    schema + the team's few-shot examples (manifest-owned, passed here)."""

    envelope: ContextEnvelopeModel
    agent_key: str
    connection_key: str
    question: str
    examples: list[dict[str, str]] = []  # [{question, sql}]


class DataAnswerModel(StrictBaseModel):
    text: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    warnings: list[str]
    answerable: bool = True
    reason: str = ""


class McpToolsRequest(StrictBaseModel):
    envelope: ContextEnvelopeModel
    agent_key: str
    connection_key: str


class McpCallRequest(StrictBaseModel):
    envelope: ContextEnvelopeModel
    agent_key: str
    connection_key: str
    tool: str
    arguments: dict[str, Any] = {}


class McpToolModel(StrictBaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class McpCallResponse(StrictBaseModel):
    is_error: bool
    text: str
    structured: dict[str, Any]


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


class AgentResolveRequest(StrictBaseModel):
    """M5 delegation handshake: the calling agent asks the platform to
    resolve a peer for consultation. The platform guards the hop (Casbin on
    the END USER's roles vs the peer, yaml allowlist mirror of the caller's
    manifest-declared peers, depth cap, cycle rejection) and returns the
    peer's address plus a FRESH platform-signed envelope with the chain
    extended server-side."""

    envelope: ContextEnvelopeModel
    agent_key: str
    peer_key: str


class AgentResolveResponse(StrictBaseModel):
    peer_key: str
    display_name: str
    card_url: str
    # aubrey.context/v1 metadata block: identity fields + delegated_from
    # extended with the caller's key + fresh sig/issued_at. Forward to the
    # peer verbatim (window/memory may be added — they are not signed).
    envelope: dict[str, Any]


class CatalogAgentModel(StrictBaseModel):
    agent_key: str
    display_name: str
    description: str
    team_key: str


class CatalogResponse(StrictBaseModel):
    agents: list[CatalogAgentModel]
