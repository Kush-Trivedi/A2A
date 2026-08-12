import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from ..utils.azure.azure_helpers import AzureKeyVaultSecretStore
from ..utils.common.logger import Logger

logger = Logger(__name__).get_logger()


class ApplicationContext:
    """Centralized configuration: ENV picks env/<ENV>.yaml, same keys in
    every environment. An env var override (AUBREY_<SECTION>_<KEY>) wins,
    then `lookup:<secret-name>` resolves from Azure Key Vault, then the yaml
    value. Missing required sections fail at startup — no fallbacks."""

    def __init__(self) -> None:
        self._keyvault_client: Optional[AzureKeyVaultSecretStore] = None
        self.environment = os.getenv("ENV", "local")
        self.file_path = Path(__file__).parent / "env" / f"{self.environment}.yaml"

        if not self.file_path.is_file():
            raise FileNotFoundError(f"Configuration file not found: {self.file_path}")

        with self.file_path.open("r", encoding="utf-8-sig") as file:
            content = yaml.safe_load(file)

        raw_keyvault = content["microsoft"]["azure"]["keyvault"]
        self._keyvault_url = os.getenv("AUBREY_KEYVAULT_URL") or raw_keyvault.get("keyvault_url", "")
        self._keyvault_secret_prefix = os.getenv("AUBREY_KEYVAULT_SECRET_PREFIX") or raw_keyvault.get(
            "keyvault_secret_prefix", ""
        )
        self._managed_identity_client_id = str(
            content["microsoft"]["azure"].get("managed_identity_client_id", "") or ""
        )

        self.config = content["config"]
        self.server = content["server"]
        self.database = content["database"]
        self.microsoft = content["microsoft"]
        self.security = content["security"]
        self.authorization = content["authorization"]
        self.knowledge = content["knowledge"]
        logger.info("Configuration loaded for environment: %s", self.environment)

    def _value_with_overrides(
        self,
        variable: str,
        value: Any,
        lookup: Optional[AzureKeyVaultSecretStore] = None,
        env_var: Optional[str] = None,
    ) -> Any:
        environment_name = env_var or f"AUBREY_{variable.upper()}"
        environment_value = os.getenv(environment_name)
        if environment_value:
            return environment_value

        if isinstance(value, str) and value.lower().startswith("lookup"):
            if lookup is None:
                raise ValueError(
                    f"'{variable}' uses a Key Vault lookup but no vault is "
                    "configured. Set microsoft.azure.keyvault.keyvault_url."
                )
            _, _, lookup_key = value.partition(":")
            return lookup.get_secret(lookup_key.strip() or variable)
        return value

    def _get_keyvault_client(self) -> AzureKeyVaultSecretStore:
        if self._keyvault_client is None:
            self._keyvault_client = AzureKeyVaultSecretStore(
                vault_url=self._keyvault_url,
                secret_prefix=self._keyvault_secret_prefix,
                managed_identity_client_id=self._managed_identity_client_id or None,
            )
        return self._keyvault_client

    @property
    def managed_identity_client_id(self) -> str:
        return self._managed_identity_client_id

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

    @property
    def config(self) -> Dict[str, Any]:
        return self._config

    @config.setter
    def config(self, variables: Dict[str, Any]) -> None:
        self._config = self._resolve_section("AUBREY", variables or {})

    @property
    def server(self) -> Dict[str, Any]:
        return self._server

    @server.setter
    def server(self, variables: Dict[str, Any]) -> None:
        self._server = self._resolve_section("AUBREY_SERVER", variables)

    @property
    def database(self) -> Dict[str, Any]:
        return self._database

    @database.setter
    def database(self, variables: Dict[str, Any]) -> None:
        self._database = self._resolve_section("ACE_DB", variables)

    @property
    def microsoft(self) -> Dict[str, Any]:
        return self._microsoft

    @microsoft.setter
    def microsoft(self, variables: Dict[str, Any]) -> None:
        self._microsoft = self._resolve_section("AUBREY_MICROSOFT", variables)

    @property
    def security(self) -> Dict[str, Any]:
        return self._security

    @security.setter
    def security(self, variables: Dict[str, Any]) -> None:
        self._security = self._resolve_section("AUBREY_SECURITY", variables)

    @property
    def authorization(self) -> Dict[str, Any]:
        return self._authorization

    @authorization.setter
    def authorization(self, variables: Dict[str, Any]) -> None:
        self._authorization = self._resolve_section("AUBREY_AUTHZ", variables)

    @property
    def knowledge(self) -> Dict[str, Any]:
        return self._knowledge

    @knowledge.setter
    def knowledge(self, variables: Dict[str, Any]) -> None:
        self._knowledge = self._resolve_section("AUBREY_KNOWLEDGE", variables)


@lru_cache(maxsize=1)
def get_application_context() -> ApplicationContext:
    return ApplicationContext()
