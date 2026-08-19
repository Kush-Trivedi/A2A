"""Semantic memory — stable facts about the user/domain ("member id
[REDACTED-mrn]", "prefers Spanish"), mined by the extractor after each
turn and recalled by cosine similarity to the rewritten question. All of
the §8 mechanics (redact -> embed -> encrypt, age-based decay) live in the
shared PgVectorMemoryLayer; this class only binds the table, the top-k,
and the facts half-life."""

from ....entity.memory import MemoryFactEntity
from ..scope import MemoryScope
from .vector_layer import PgVectorMemoryLayer


class SemanticMemoryLayer(PgVectorMemoryLayer):
    name = "semantic"
    _table = "memory_facts"
    _half_life_key = "facts"

    def _top_k(self) -> int:
        return self._settings.facts_top_k

    def _make_entity(
        self, scope: MemoryScope, *, content: str, embedding: list[float],
        weight: float, source: str,
    ) -> MemoryFactEntity:
        return MemoryFactEntity(
            id=self._new_id(),
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            content=content,
            embedding=embedding,
            weight=weight,
            source=source,
        )


_layer: SemanticMemoryLayer | None = None


def get_semantic_memory_layer() -> SemanticMemoryLayer:
    global _layer
    if _layer is None:
        _layer = SemanticMemoryLayer()
    return _layer
