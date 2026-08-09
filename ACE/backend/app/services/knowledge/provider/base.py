from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    source_name: str
    knowledge_source: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestChunk:
    chunk_index: int
    content: str
    embedding: list[float]
    token_count: int
    embedding_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestDocument:
    tenant_id: str
    actor_id: str
    knowledge_source: str
    source_name: str
    source_type: str
    sha256: str
    size_bytes: int
    embedding_model: str
    embedding_dimensions: int
    chunks: tuple[IngestChunk, ...]
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalQuery:
    tenant_id: str
    embedding: list[float]
    top_k: int
    knowledge_sources: tuple[str, ...]
    min_similarity: float = 0.0
    session_id: str | None = None
    query_text: str = ""
    candidate_pool: int = 32
    neighbor_window: int = 0
    neighbor_max_window: int = 0
    neighbor_score_floor: float = 0.0
    context_token_budget: int = 0
    mode: str = ""


class KnowledgeProvider(ABC):
    @abstractmethod
    async def upsert(self, document: IngestDocument) -> str:
        """Persist a document + its chunks; return the new document id."""

    @abstractmethod
    async def find_document_by_hash(
        self,
        *,
        tenant_id: str,
        sha256: str,
        knowledge_source: str,
        session_id: str | None = None,
    ) -> tuple[str, int] | None:
        """Return the document id and chunk count for a document with the given hash, or None if not found."""

    @abstractmethod
    async def retrieve(self, query: RetrievalQuery) -> list[KnowledgeChunk]:
        """Retrieve the top-k chunks for a given query."""

    @abstractmethod
    async def soft_delete_session_uploads(
        self, *, tenant_id: str, session_id: str
    ) -> int:
        """Archive documents uploaded within session Soft delete all documents and chunks associated with a given session id; return the number of documents deleted."""
