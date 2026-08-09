from dataclasses import dataclass
from typing import Any

from .application_context import ApplicationContext, get_application_context
from ..utils.common.logger import Logger

logger = Logger(__name__).get_logger()


class PlaceholderPolicy:
    """Single source of truth for detecting template placeholder values.

    A value like "your_tenant_id" means the yaml key was never filled in.
    The system must never special-case these to "work anyway" — it reports
    them precisely so swapping in real credentials is a pure yaml change.
    """

    _PREFIX = "your_"

    @classmethod
    def is_placeholder(cls, value: Any) -> bool:
        return isinstance(value, str) and value.strip().lower().startswith(cls._PREFIX)

    @classmethod
    def is_empty(cls, value: Any) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    @classmethod
    def is_configured(cls, value: Any) -> bool:
        return not cls.is_empty(value) and not cls.is_placeholder(value)


@dataclass(frozen=True)
class SettingsFinding:
    path: str
    severity: str  # "warning" | "error"
    message: str


@dataclass(frozen=True)
class SettingsValidationReport:
    findings: tuple[SettingsFinding, ...]

    @property
    def warnings(self) -> tuple[SettingsFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def errors(self) -> tuple[SettingsFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def is_clean(self) -> bool:
        return not self.findings


class SettingsValidator:
    """Validates the resolved configuration at startup.

    Reports (never hides): required keys that are missing/empty, and any value
    still carrying the yaml template placeholder pattern. Findings name the
    exact dotted yaml path so fixing them is mechanical.
    """

    _REQUIRED_PATHS: tuple[str, ...] = (
        "server.host",
        "server.port",
        "database.postgres.host",
        "database.postgres.user",
        "database.postgres.password",
        "database.postgres.dbname",
        "microsoft.entra.tenant_id",
        "microsoft.entra.client_id",
    )

    def __init__(self, context: ApplicationContext | None = None) -> None:
        self._context = context or get_application_context()

    def _sections(self) -> dict[str, Any]:
        return {
            "config": self._context.config,
            "server": self._context.server,
            "database": self._context.database,
            "databricks": self._context.databricks,
            "langfuse": self._context.langfuse,
            "twilio": self._context.twilio,
            "google": self._context.google,
            "microsoft": self._context.microsoft,
            "security": self._context.security,
            "authorization": self._context.authorization,
            "knowledge": self._context.knowledge,
            "agents": self._context.agents,
        }

    def validate(self) -> SettingsValidationReport:
        sections = self._sections()
        findings: list[SettingsFinding] = []
        findings.extend(self._find_placeholders(sections))
        findings.extend(self._find_missing_required(sections))
        return SettingsValidationReport(findings=tuple(findings))

    def validate_and_log(self) -> SettingsValidationReport:
        report = self.validate()
        for finding in report.errors:
            logger.error(
                "[red]Configuration error at '%s': %s", finding.path, finding.message
            )
        for finding in report.warnings:
            logger.warning(
                "[yellow]Configuration warning at '%s': %s", finding.path, finding.message
            )
        if report.is_clean:
            logger.info("[green]Configuration validated: no placeholders, no missing keys.")
        else:
            logger.info(
                "Configuration validated with %d error(s) and %d warning(s).",
                len(report.errors),
                len(report.warnings),
            )
        return report

    def _find_placeholders(self, sections: dict[str, Any]) -> list[SettingsFinding]:
        findings: list[SettingsFinding] = []
        for name, data in sections.items():
            self._walk(name, data, findings)
        return findings

    def _walk(self, path: str, value: Any, findings: list[SettingsFinding]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                self._walk(f"{path}.{key}", child, findings)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                self._walk(f"{path}[{index}]", child, findings)
        elif PlaceholderPolicy.is_placeholder(value):
            findings.append(
                SettingsFinding(
                    path=path,
                    severity="warning",
                    message="Value still holds the yaml template placeholder; replace it with the real value.",
                )
            )

    def _find_missing_required(self, sections: dict[str, Any]) -> list[SettingsFinding]:
        findings: list[SettingsFinding] = []
        for dotted in self._REQUIRED_PATHS:
            value = self._resolve(sections, dotted)
            if PlaceholderPolicy.is_empty(value):
                findings.append(
                    SettingsFinding(
                        path=dotted,
                        severity="error",
                        message="Required configuration key is missing or empty.",
                    )
                )
        return findings

    @staticmethod
    def _resolve(sections: dict[str, Any], dotted: str) -> Any:
        node: Any = sections
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node


_validator: SettingsValidator | None = None


def get_settings_validator() -> SettingsValidator:
    global _validator
    if _validator is None:
        _validator = SettingsValidator()
    return _validator
