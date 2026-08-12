"""Prompts come from the team manifest, never Python. Missing placeholders
survive formatting (a team can add {variables} the code doesn't know)."""


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PromptStore:
    def __init__(self, prompts: dict) -> None:
        self._prompts = dict(prompts or {})

    def get(self, name: str) -> str | None:
        entry = self._prompts.get(name)
        if entry is None:
            return None
        if isinstance(entry, dict):
            return str(entry.get("content") or "") or None
        return str(entry) or None

    def render(self, name: str, **variables: object) -> str | None:
        template = self.get(name)
        if template is None:
            return None
        return template.format_map(_SafeDict(**variables)).strip()
