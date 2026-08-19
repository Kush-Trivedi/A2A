"""The layer contract the whole memory system programs against.

The orchestrator runs layers in parallel under per-layer timeouts; a layer
that raises or misses its deadline contributes NOTHING this turn — recall
must therefore be side-effect free. The question is embedded ONCE by the
orchestrator and shared through RecallQuery, so N layers never pay N
embedding calls; a None vector means the embedding endpoint is absent
(local dev) and vector-based layers degrade to empty rather than fail."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .record import MemoryRecord
from .scope import MemoryScope


@dataclass(frozen=True)
class RecallQuery:
    text: str  # the (rewritten) question
    vector: tuple[float, ...] | None = None  # shared question embedding


class MemoryLayer(ABC):
    name: str = ""

    @abstractmethod
    async def recall(
        self, scope: MemoryScope, query: RecallQuery, budget_tokens: int
    ) -> list[MemoryRecord]:
        """Most relevant records for this scope+question, best first."""

    @abstractmethod
    async def record(self, scope: MemoryScope, records: list[MemoryRecord]) -> None:
        """Persist new records (redact -> embed -> encrypt -> insert)."""

    @abstractmethod
    async def decay(self) -> int:
        """Periodic maintenance: age weights, prune the floor. Returns pruned."""
