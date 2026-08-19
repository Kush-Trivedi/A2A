"""The unit every memory layer speaks — recall returns these, record
accepts these. Content here is always PLAINTEXT (already redacted on the
write path); encryption is a storage concern inside each layer, never
visible at this boundary. Frozen: records are immutable in transit just
as they are at rest (append + decay, never rewrite)."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class MemoryRecord:
    layer: str  # working | semantic | episodic | procedural | prospective
    content: str
    weight: float  # decayed relevance, 0..1
    created_at: datetime
    source: str  # extractor | session | feedback | manifest | scheduler
    metadata: dict = field(default_factory=dict)
