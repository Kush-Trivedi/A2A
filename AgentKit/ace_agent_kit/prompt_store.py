from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptDefinition:
    name: str
    version: str
    content: str

    def format(self, **variables: Any) -> str:
        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return self.content.format_map(_SafeDict(variables))


class PromptStore:
    def __init__(self, prompts: Mapping[str, PromptDefinition]) -> None:
        self._prompts = dict(prompts)

    @classmethod
    def from_manifest(cls, raw: Mapping[str, Any] | None) -> "PromptStore":
        prompts: dict[str, PromptDefinition] = {}
        for name, entry in (raw or {}).items():
            if not isinstance(entry, Mapping):
                continue
            content = str(entry.get("content", "") or "")
            if not content.strip():
                continue
            prompts[str(name)] = PromptDefinition(
                name=str(name),
                version=str(entry.get("version", "1.0.0") or "1.0.0"),
                content=content,
            )
        return cls(prompts)

    def get(self, name: str) -> PromptDefinition | None:
        return self._prompts.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._prompts))

    def to_registration_payload(self) -> dict[str, dict[str, str]]:
        return {
            p.name: {"version": p.version, "content": p.content}
            for p in self._prompts.values()
        }
