from dataclasses import dataclass
from functools import lru_cache
from ...config.application_context import get_application_context
from ...entity.pgvector.vector_type import DEFAULT_EMBEDDING_DIMENSIONS

def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

@dataclass(frozen=True)
class KnowledgeSettings:
    embedding_dimensions: int
    chunking_strategy: str
    chunk_size: int
    chunk_overlap: int
    chunk_min_size: int
    chunk_target_count: int
    semantic_breakpoint_percentile: float
    embedding_batch_size: int
    retrieval_mode: str
    retrieval_top_k: int
    retrieval_candidates: int
    min_similarity: float
    rrf_k: int
    neighbor_window: int
    neighbor_decay: float
    neighbor_max_window: int
    neighbor_score_floor: float
    context_token_budget: int
    resource_prefix: str
    read_action: str
    write_action: str
    upload_source: str

    @property
    def hybrid_enabled(self) -> bool:
        return self.retrieval_mode == "hybrid"


@lru_cache(maxsize=1)
def get_knowledge_settings() -> KnowledgeSettings:
    ac = get_application_context()
    embedding_cfg = ac.microsoft["azure"]["azure_foundry"].get("embedding", {})
    knowledge = ac.knowledge
    chunking = knowledge.get("chunking", {}) or {}
    retrieval = knowledge.get("retrieval", {}) or {}
    access = knowledge.get("access", {}) or {}

    return KnowledgeSettings(
        embedding_dimensions=_as_int(
            embedding_cfg.get("embedding_dimensions"), DEFAULT_EMBEDDING_DIMENSIONS
        ),
        chunking_strategy=str(chunking.get("strategy") or "recursive").strip().lower(),
        chunk_size=_as_int(chunking.get("chunk_size"), 1000),
        chunk_overlap=_as_int(chunking.get("chunk_overlap"), 200),
        chunk_min_size=_as_int(chunking.get("chunk_min_size"), 128),
        chunk_target_count=_as_int(chunking.get("chunk_target_count"), 8),
        semantic_breakpoint_percentile=_as_float(
            chunking.get("semantic_breakpoint_percentile"), 95.0
        ),
        embedding_batch_size=_as_int(embedding_cfg.get("embedding_batch_size"), 50),
        retrieval_mode=str(retrieval.get("mode") or "hybrid").strip().lower(),
        retrieval_top_k=_as_int(retrieval.get("top_k"), 8),
        retrieval_candidates=_as_int(retrieval.get("candidates"), 32),
        min_similarity=_as_float(retrieval.get("min_similarity"), 0.0),
        rrf_k=_as_int(retrieval.get("rrf_k"), 60),
        neighbor_window=_as_int(retrieval.get("neighbor_window"), 1),
        neighbor_decay=_as_float(retrieval.get("neighbor_decay"), 0.92),
        neighbor_max_window=_as_int(retrieval.get("neighbor_max_window"), 3),
        neighbor_score_floor=_as_float(retrieval.get("neighbor_score_floor"), 0.5),
        context_token_budget=_as_int(retrieval.get("context_token_budget"), 0),
        resource_prefix=str(access.get("resource_prefix") or "knowledge:"),
        read_action=str(access.get("read_action") or "read"),
        write_action=str(access.get("write_action") or "write"),
        upload_source=str(access.get("upload_source") or "upload"),
    )
