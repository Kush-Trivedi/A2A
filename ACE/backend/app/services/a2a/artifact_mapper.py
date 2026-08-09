from dataclasses import dataclass, field
from typing import Any

from a2a.types import Artifact

from .part_mapper import PartMapper


@dataclass(frozen=True)
class MappedArtifact:
    artifact_id: str
    name: str
    description: str
    parts: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "description": self.description,
            "parts": list(self.parts),
        }


class ArtifactMapper:
    """Maps A2A artifacts into the shape the chat canvas renders."""

    def __init__(self, part_mapper: PartMapper | None = None) -> None:
        self._parts = part_mapper or PartMapper()

    def map(self, artifact: Artifact) -> MappedArtifact:
        return MappedArtifact(
            artifact_id=artifact.artifact_id,
            name=artifact.name,
            description=artifact.description,
            parts=tuple(self._parts.part_payload(part) for part in artifact.parts),
        )
