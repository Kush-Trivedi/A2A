"""Episodic memory — summaries of past interactions ("2026-07: appealed
knee MRI denial, resolved"), recalled across sessions by similarity.
Storage and recall mechanics are the shared PgVectorMemoryLayer; this
class binds the table, top-k, the episodes half-life, and provenance
(the session an episode came from). Population lands in M10c when session
close/topic-shift detection exists — until then recall simply returns
empty, which the orchestrator already treats as a layer with nothing to
say."""

from ....entity.memory import MemoryEpisodeEntity
from ..scope import MemoryScope
from .vector_layer import PgVectorMemoryLayer


class EpisodicMemoryLayer(PgVectorMemoryLayer):
    name = "episodic"
    _table = "memory_episodes"
    _half_life_key = "episodes"

    def _top_k(self) -> int:
        return self._settings.episodes_top_k

    def _make_entity(
        self, scope: MemoryScope, *, content: str, embedding: list[float],
        weight: float, source: str,
    ) -> MemoryEpisodeEntity:
        return MemoryEpisodeEntity(
            id=self._new_id(),
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            session_id=scope.session_id,
            content=content,
            embedding=embedding,
            weight=weight,
            source=source,
        )


_layer: EpisodicMemoryLayer | None = None


def get_episodic_memory_layer() -> EpisodicMemoryLayer:
    global _layer
    if _layer is None:
        _layer = EpisodicMemoryLayer()
    return _layer
