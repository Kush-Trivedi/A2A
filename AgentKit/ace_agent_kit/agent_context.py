"""Agent-side configuration context — the ApplicationContext twin.

The exact discipline ACE uses, ported for team agents:

- ``ENV`` (local|dev|uat|prd) picks ``config/env/<ENV>.yaml``. Same keys in
  every file; only VALUES differ. There is no "local mode" — one code path.
- An environment variable override always wins (``AGENT_<SECTION>_<KEY>``).
- A yaml value starting with ``lookup`` resolves from the team's Azure Key
  Vault (``lookup:my-secret-name``), authenticated with
  DefaultAzureCredential (managed identity in Container Apps / AKS).
- ``your_*`` values are template placeholders: reported precisely at startup
  so swapping in real values is a pure yaml change, never a code change.
- ``agent.yaml`` (the manifest: identity, skills, prompts, routing examples)
  is environment-INVARIANT and loaded as-is — it is the team's product
  definition, not runtime configuration.
"""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


class PlaceholderPolicy:
    """Single source of truth for detecting template placeholder values."""

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


class KeyVaultSecretStore:
    """Azure Key Vault lookup with the same secret-name normalization ACE uses.

    Secret names: underscores become hyphens; an optional prefix is applied
    (supports a literal ``{key}`` slot, otherwise ``<prefix>-<key>``).
    """

    def __init__(
        self,
        vault_url: Optional[str] = None,
        secret_prefix: Optional[str] = None,
        managed_identity_client_id: Optional[str] = None,
    ) -> None:
        if not vault_url:
            raise ValueError(
                "Azure Key Vault URL is required. Set azure.keyvault.keyvault_url "
                "in the agent's config/env/<ENV>.yaml."
            )
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        self.vault_url = vault_url
        self.secret_prefix = secret_prefix or ""
        self._client = SecretClient(
            vault_url=self.vault_url,
            credential=DefaultAzureCredential(
                managed_identity_client_id=managed_identity_client_id,
            ),
        )

    def _full_secret_name(self, key: str) -> str:
        normalized_key = key.replace("_", "-")
        prefix = self.secret_prefix.strip().replace("_", "-")
        if not prefix:
            return normalized_key
        if "{key}" in prefix:
            return prefix.format(key=normalized_key)
        separator = "" if prefix.endswith("-") else "-"
        return f"{prefix}{separator}{normalized_key}"

    def get_secret(self, key: str) -> str:
        secret = self._client.get_secret(self._full_secret_name(key))
        return secret.value or ""


class AgentContext:
    """Loads and resolves the agent's per-environment configuration.

    File locations (overridable for containers/tests):
    - env config:  ``<AGENT_CONFIG_DIR or ./config/env>/<ENV>.yaml``
    - manifest:    ``AGENT_MANIFEST_PATH or ./agent.yaml``
    """

    _ENV_PREFIX = "AGENT"

    def __init__(
        self,
        config_dir: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ) -> None:
        self._keyvault_client: Optional[KeyVaultSecretStore] = None
        self.environment = os.getenv("ENV", "local")

        base = Path(config_dir or os.getenv("AGENT_CONFIG_DIR") or Path.cwd() / "config" / "env")
        self.file_path = base / f"{self.environment}.yaml"
        if not self.file_path.is_file():
            raise FileNotFoundError(
                f"Agent configuration file not found: {self.file_path} "
                f"(ENV={self.environment}). Every environment ships the same "
                f"config/env/<ENV>.yaml keys — only values differ."
            )

        with self.file_path.open("r", encoding="utf-8-sig") as file:
            content: Dict[str, Any] = yaml.safe_load(file) or {}

        raw_azure = (content.get("azure") or {})
        raw_keyvault = raw_azure.get("keyvault") or {}
        self._keyvault_url = os.getenv("AGENT_KEYVAULT_URL") or str(
            raw_keyvault.get("keyvault_url", "") or ""
        )
        self._keyvault_secret_prefix = os.getenv("AGENT_KEYVAULT_SECRET_PREFIX") or str(
            raw_keyvault.get("keyvault_secret_prefix", "") or ""
        )
        self._managed_identity_client_id = str(
            raw_azure.get("managed_identity_client_id", "") or ""
        )

        self.server = content.get("server", {}) or {}
        self.ace = content.get("ace", {}) or {}
        self.auth = content.get("auth", {}) or {}
        self.llm = content.get("llm", {}) or {}
        self.retrieval = content.get("retrieval", {}) or {}
        self.connections = content.get("connections", {}) or {}
        self.channels = content.get("channels", {}) or {}

        manifest_file = Path(
            manifest_path or os.getenv("AGENT_MANIFEST_PATH") or Path.cwd() / "agent.yaml"
        )
        if not manifest_file.is_file():
            raise FileNotFoundError(
                f"Agent manifest not found: {manifest_file}. The manifest "
                f"(agent.yaml) is environment-invariant and always ships with the agent."
            )
        with manifest_file.open("r", encoding="utf-8-sig") as file:
            self._manifest: Dict[str, Any] = yaml.safe_load(file) or {}
        self.manifest_path = manifest_file

        logger.info(
            "Agent configuration loaded for environment '%s' from %s",
            self.environment,
            self.file_path,
        )

    # -- resolution (identical semantics to ACE's ApplicationContext) --------

    def _value_with_overrides(
        self,
        variable: str,
        value: Any,
        lookup: Optional[KeyVaultSecretStore] = None,
        env_var: Optional[str] = None,
    ) -> Any:
        result = value
        environment_name = env_var or f"{self._ENV_PREFIX}_{variable.upper()}"
        environment_value = os.getenv(environment_name)
        if environment_value:
            logger.debug("Overrode '%s' from environment %s.", variable, environment_name)
            return environment_value

        if isinstance(value, str) and value.lower().startswith("lookup"):
            if lookup is None:
                logger.warning(
                    "No Key Vault available for '%s' which requires a lookup override.",
                    variable,
                )
            else:
                _, _, lookup_key = value.partition(":")
                result = lookup.get_secret(lookup_key.strip() or variable)
                logger.debug("Resolved '%s' via Azure Key Vault lookup.", variable)
        return result

    def _get_keyvault_client(self) -> KeyVaultSecretStore:
        if self._keyvault_client is None:
            self._keyvault_client = KeyVaultSecretStore(
                vault_url=self._keyvault_url,
                secret_prefix=self._keyvault_secret_prefix,
                managed_identity_client_id=self._managed_identity_client_id or None,
            )
        return self._keyvault_client

    def _resolve_section(self, prefix: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for variable, value in variables.items():
            env_var = f"{prefix}_{variable}".upper()
            if isinstance(value, dict):
                result[variable] = self._resolve_section(env_var, value)
                continue
            lookup = None
            if (
                isinstance(value, str)
                and value.lower().startswith("lookup")
                and not os.getenv(env_var)
            ):
                lookup = self._get_keyvault_client()
            result[variable] = self._value_with_overrides(
                variable, value, lookup=lookup, env_var=env_var
            )
        return result

    # -- sections ------------------------------------------------------------

    @property
    def manifest(self) -> Dict[str, Any]:
        return self._manifest

    @property
    def managed_identity_client_id(self) -> str:
        return self._managed_identity_client_id

    @property
    def server(self) -> Dict[str, Any]:
        return self._server

    @server.setter
    def server(self, variables: Dict[str, Any]) -> None:
        self._server = self._resolve_section("AGENT_SERVER", variables)

    @property
    def ace(self) -> Dict[str, Any]:
        return self._ace

    @ace.setter
    def ace(self, variables: Dict[str, Any]) -> None:
        self._ace = self._resolve_section("AGENT_ACE", variables)

    @property
    def auth(self) -> Dict[str, Any]:
        return self._auth

    @auth.setter
    def auth(self, variables: Dict[str, Any]) -> None:
        self._auth = self._resolve_section("AGENT_AUTH", variables)

    @property
    def llm(self) -> Dict[str, Any]:
        return self._llm

    @llm.setter
    def llm(self, variables: Dict[str, Any]) -> None:
        self._llm = self._resolve_section("AGENT_LLM", variables)

    @property
    def retrieval(self) -> Dict[str, Any]:
        return self._retrieval

    @retrieval.setter
    def retrieval(self, variables: Dict[str, Any]) -> None:
        self._retrieval = self._resolve_section("AGENT_RETRIEVAL", variables)

    @property
    def connections(self) -> Dict[str, Any]:
        return self._connections

    @connections.setter
    def connections(self, variables: Dict[str, Any]) -> None:
        self._connections = self._resolve_section("AGENT_CONNECTIONS", variables)

    @property
    def channels(self) -> Dict[str, Any]:
        return self._channels

    @channels.setter
    def channels(self, variables: Dict[str, Any]) -> None:
        self._channels = self._resolve_section("AGENT_CHANNELS", variables)

    def section(self, name: str) -> Dict[str, Any]:
        """Generic access for future sections without a dedicated property."""
        attr = f"_{name}"
        if hasattr(self, attr):
            return getattr(self, attr)
        raise KeyError(f"Unknown configuration section '{name}'.")


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


class AgentSettingsValidator:
    """Startup validation with ACE's reporting discipline: every finding
    names the exact dotted yaml path, so fixing config is mechanical."""

    _REQUIRED_PATHS: tuple[str, ...] = (
        "server.host",
        "server.port",
        "ace.base_url",
        "ace.public_url",
    )

    def __init__(self, context: AgentContext | None = None) -> None:
        self._context = context or get_agent_context()

    def _sections(self) -> Dict[str, Any]:
        return {
            "server": self._context.server,
            "ace": self._context.ace,
            "auth": self._context.auth,
            "llm": self._context.llm,
            "retrieval": self._context.retrieval,
            "connections": self._context.connections,
            "channels": self._context.channels,
        }

    def validate(self) -> SettingsValidationReport:
        sections = self._sections()
        findings: list[SettingsFinding] = []
        for name, data in sections.items():
            self._walk(name, data, findings)
        for dotted in self._REQUIRED_PATHS:
            if PlaceholderPolicy.is_empty(self._resolve(sections, dotted)):
                findings.append(
                    SettingsFinding(
                        path=dotted,
                        severity="error",
                        message="Required configuration key is missing or empty.",
                    )
                )
        return SettingsValidationReport(findings=tuple(findings))

    def validate_and_log(self) -> SettingsValidationReport:
        report = self.validate()
        for finding in report.errors:
            logger.error("Configuration error at '%s': %s", finding.path, finding.message)
        for finding in report.warnings:
            logger.warning("Configuration warning at '%s': %s", finding.path, finding.message)
        if report.is_clean:
            logger.info("Agent configuration validated: no placeholders, no missing keys.")
        return report

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

    @staticmethod
    def _resolve(sections: Dict[str, Any], dotted: str) -> Any:
        node: Any = sections
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node


@lru_cache(maxsize=1)
def get_agent_context() -> AgentContext:
    return AgentContext()
