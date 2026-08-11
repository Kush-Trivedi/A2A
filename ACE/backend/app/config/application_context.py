import os
import yaml
from pathlib import Path
from functools import lru_cache
from rich.console import Console
from typing import Any, Dict, Optional
from ..utils.common.logger import Logger
from ..utils.azure.azure_helpers import AzureKeyVaultSecretStore

logger = Logger().get_logger()
console = Console()



class ApplicationContext:
    def __init__(self) -> None:
        self._keyvault_client: Optional[AzureKeyVaultSecretStore] = None
        self.environment = os.getenv("ENV", "local")
        self.file_path = Path(__file__).parent / "env" / f"{self.environment}.yaml"

        console.print(f"Loading configuration from: {self.file_path}", style="cyan")

        if not self.file_path.is_file():
            logger.error(f"[red]Configuration file not found: {self.file_path}")
            console.print(
                f"Configuration file not found: {self.file_path}",
                style="red",
            )
            raise FileNotFoundError(f"{self.file_path} is not a valid file path.")

        try:
            with self.file_path.open("r", encoding="utf-8-sig") as file:
                content = yaml.safe_load(file)
        except Exception as e:
            logger.error(
                f"[red]Failed to load YAML file {self.file_path}: {e}",
                exc_info=True,
            )
            console.print(
                f"Failed to load YAML file {self.file_path}: {e}",
                style="red",
            )
            raise

        try:
            raw_keyvault = content["microsoft"]["azure"]["keyvault"]
            self._keyvault_url = os.getenv("ACE_KEYVAULT_URL") or raw_keyvault.get("keyvault_url", "")
            self._keyvault_secret_prefix = os.getenv("ACE_KEYVAULT_SECRET_PREFIX") or raw_keyvault.get("keyvault_secret_prefix", "")
            self._managed_identity_client_id = str(
                content["microsoft"]["azure"].get("managed_identity_client_id", "") or ""
            )

            self.config = content["config"]
            self.server = content["server"]
            self.database = content["database"]
            # Team-owned integrations left the platform yaml (connection
            # registry owns them now) — absent sections resolve to {}.
            self.databricks = content.get("databricks", {}) or {}
            self.langfuse = content.get("langfuse", {}) or {}
            self.twilio = content.get("twilio", {}) or {}
            self.google = content.get("google", {}) or {}
            self.microsoft = content["microsoft"]
            self.security = content.get("security", {}) or {}
            self.authorization = content.get("authorization", {}) or {}
            self.knowledge = content.get("knowledge", {}) or {}
            self.agents = content.get("agents", {}) or {}
            logger.info(f"[green]Configuration loaded successfully for environment: {self.environment}")
        except KeyError as e:
            logger.error(
                f"[red]Missing expected key in configuration: {e}",
                exc_info=True,
            )
            console.print(
                f"Missing expected key in configuration: {e}",
                style="red",
            )
            raise

    @staticmethod
    def _value_with_overrides(
        variable: str,
        value: Any,
        lookup: Optional[Any] = None,
        env_var: Optional[str] = None,
    ) -> Any:
        result = value
        environment_name = env_var or f"ACE_{variable.upper()}"
        environment_value = os.getenv(environment_name)

        if environment_value:
            logger.debug(f"[blue]Overrode '{variable}' with environment value from {environment_name}.")
            console.print(
                f"Overrode '{variable}' with environment value from {environment_name}.",
                style="bright_cyan",
            )
            return environment_value

        try:
            if isinstance(value, str) and value.lower().startswith("lookup"):
                if lookup is None:
                    logger.warning(
                        f"[yellow]No lookup provided for variable '{variable}' which requires a lookup override."
                    )
                    console.print(
                        f"No lookup provided for variable '{variable}' which requires a lookup override.",
                        style="bright_yellow",
                    )
                else:
                    _, _, lookup_key = value.partition(":")
                    result = lookup.get_secret(lookup_key.strip() or variable)

                    logger.debug(f"[blue]Override '{variable}' via Azure Key Vault lookup.")
                    console.print(
                        f"Override '{variable}' via Azure Key Vault lookup.",
                        style="bright_cyan",
                    )

        except Exception as e:
            logger.error(
                f"[red]Error during lookup for '{variable}': {e}",
                exc_info=True,
            )
            console.print(
                f"Error during lookup for '{variable}': {e}",
                style="red",
            )
            raise

        return result

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
            if isinstance(value, str) and value.lower().startswith("lookup") and not os.getenv(env_var):
                lookup = self._get_keyvault_client()

            result[variable] = self._value_with_overrides(variable, value, lookup=lookup, env_var=env_var)

        return result

    @property
    def config(self) -> Dict[str, Any]:
        if not hasattr(self, "_config"):
            raise ValueError("Config property is not initialized properly.")
        return self._config

    @config.setter
    def config(self, variables: Dict[str, Any]) -> None:
        self._config = {}

        for variable, value in variables.items():
            self._config[variable] = self._value_with_overrides(variable, value)

        logger.info("[green]Config configuration is set.")

    @property
    def server(self) -> Dict[str, Any]:
        if not hasattr(self, "_server"):
            raise ValueError("Server property is not initialized properly.")
        return self._server

    @server.setter
    def server(self, variables: Dict[str, Any]) -> None:
        self._server = {}

        for variable, value in variables.items():
            self._server[variable] = self._value_with_overrides(variable, value)

        logger.info("[green]Server configuration is set.")

    @property
    def database(self) -> Dict[str, Any]:
        if not hasattr(self, "_database"):
            raise ValueError("Database property is not initialized properly.")
        return self._database

    @database.setter
    def database(self, variables: Dict[str, Any]) -> None:
        self._database = self._resolve_section("ACE_DB", variables)

        logger.info("[green]Database configuration is set.")

    @property
    def databricks(self) -> Dict[str, Any]:
        if not hasattr(self, "_databricks"):
            raise ValueError("Databricks property is not initialized properly.")
        return self._databricks

    @databricks.setter
    def databricks(self, variables: Dict[str, Any]) -> None:
        self._databricks = self._resolve_section("ACE_DATABRICKS", variables)

        logger.info("[green]Databricks configuration is set.")

    @property
    def langfuse(self) -> Dict[str, Any]:
        if not hasattr(self, "_langfuse"):
            raise ValueError("Langfuse property is not initialized properly.")
        return self._langfuse

    @langfuse.setter
    def langfuse(self, variables: Dict[str, Any]) -> None:
        self._langfuse = self._resolve_section("ACE_LANGFUSE", variables)

        logger.info("[green]Langfuse configuration is set.")

    @property
    def twilio(self) -> Dict[str, Any]:
        if not hasattr(self, "_twilio"):
            raise ValueError("Twilio property is not initialized properly.")
        return self._twilio
    
    @twilio.setter
    def twilio(self, variables: Dict[str, Any]) -> None:
        self._twilio = self._resolve_section("ACE_TWILIO", variables)

        logger.info("[green]Twilio configuration is set.")

    @property
    def google(self) -> Dict[str, Any]:
        if not hasattr(self, "_google"):
            raise ValueError("Google property is not initialized properly.")
        return self._google
    
    @google.setter
    def google(self, variables: Dict[str, Any]) -> None:
        self._google = self._resolve_section("ACE_GOOGLE", variables)

        logger.info("[green]Google configuration is set.")

    @property
    def microsoft(self) -> Dict[str, Any]:
        if not hasattr(self, "_microsoft"):
            raise ValueError("Microsoft property is not initialized properly.")
        return self._microsoft

    @microsoft.setter
    def microsoft(self, variables: Dict[str, Any]) -> None:
        self._microsoft = self._resolve_section("ACE_MICROSOFT", variables)

        logger.info("[green]Microsoft configuration is set.")

    @property
    def security(self) -> Dict[str, Any]:
        if not hasattr(self, "_security"):
            raise ValueError("Security property is not initialized properly.")
        return self._security

    @security.setter
    def security(self, variables: Dict[str, Any]) -> None:
        self._security = self._resolve_section("ACE_SECURITY", variables)

        logger.info("[green]Security configuration is set.")

    @property
    def authorization(self) -> Dict[str, Any]:
        if not hasattr(self, "_authorization"):
            raise ValueError("Authorization property is not initialized properly.")
        return self._authorization

    @authorization.setter
    def authorization(self, variables: Dict[str, Any]) -> None:
        self._authorization = self._resolve_section("ACE_AUTHZ", variables)

        logger.info("[green]Authorization configuration is set.")

    @property
    def knowledge(self) -> Dict[str, Any]:
        if not hasattr(self, "_knowledge"):
            raise ValueError("Knowledge property is not initialized properly.")
        return self._knowledge

    @knowledge.setter
    def knowledge(self, variables: Dict[str, Any]) -> None:
        self._knowledge = self._resolve_section("ACE_KNOWLEDGE", variables)

        logger.info("[green]Knowledge configuration is set.")

    @property
    def agents(self) -> Dict[str, Any]:
        if not hasattr(self, "_agents"):
            raise ValueError("Agents property is not initialized properly.")
        return self._agents

    @agents.setter
    def agents(self, variables: Dict[str, Any]) -> None:
        self._agents = self._resolve_section("ACE_AGENTS", variables)

        logger.info("[green]Agents configuration is set.")


@lru_cache(maxsize=1)
def get_application_context() -> ApplicationContext:
    return ApplicationContext()
