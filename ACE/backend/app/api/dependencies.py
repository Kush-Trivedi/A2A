from ..security.oauth_client import EntraOauthClient
from ..security.session import SessionStore, get_session_store
from ..security.settings import AuthSettings, get_auth_settings
from ..services.agents import AgentRegistry, get_agent_registry
from ..services.agents.agent_catalog_service import (
    AgentCatalogService,
    get_agent_catalog_service,
)
from ..services.agents.registry_service import (
    AgentRegistryService,
    get_agent_registry_service,
)
from ..security.oauth_state import OAuthStateManager, get_oauth_state_manager
from ..security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ..services.authz import (
    AuthorizationService,
    AuthzLoginService,
    get_authorization_service,
    get_authz_login_service,
)
from ..services.conversation.conversation_service import (
    ConversationService,
    get_conversation_service,
)
from ..services.embedding.embedding_service import (
    EmbeddingService,
    get_embedding_service,
)
from ..services.embedding.ingestion_service import (
    IngestionService,
    get_ingestion_service,
)
from ..services.databricks import GenieService, get_genie_service
from ..services.health import (
    IntegrationHealthService,
    get_integration_health_service,
)
from ..services.knowledge.blob_ingestion_service import (
    BlobIngestionService,
    get_blob_ingestion_service,
)
from ..services.knowledge.sharepoint_ingestion_service import (
    SharePointIngestionService,
    get_sharepoint_ingestion_service,
)


class ServiceContainer:
    def auth_settings(self) -> AuthSettings:
        return get_auth_settings()

    def session_store(self) -> SessionStore:
        return get_session_store()

    def casbin_enforcer(self) -> CasbinEnforcer:
        return get_casbin_enforcer()

    def oauth_state_manager(self) -> OAuthStateManager:
        return get_oauth_state_manager()

    def oauth_client(self) -> EntraOauthClient:
        return EntraOauthClient(get_auth_settings())

    def login_service(self) -> AuthzLoginService:
        return get_authz_login_service()

    def authorization_service(self) -> AuthorizationService:
        return get_authorization_service()

    def conversation_service(self) -> ConversationService:
        return get_conversation_service()

    def embedding_service(self) -> EmbeddingService:
        return get_embedding_service()

    def ingestion_service(self) -> IngestionService:
        return get_ingestion_service()

    def agent_registry(self) -> AgentRegistry:
        return get_agent_registry()

    def agent_registry_service(self) -> AgentRegistryService:
        return get_agent_registry_service()

    def integration_health_service(self) -> IntegrationHealthService:
        return get_integration_health_service()

    def agent_catalog_service(self) -> AgentCatalogService:
        return get_agent_catalog_service()

    def genie_service(self) -> GenieService:
        return get_genie_service()

    def sharepoint_ingestion_service(self) -> SharePointIngestionService:
        return get_sharepoint_ingestion_service()

    def blob_ingestion_service(self) -> BlobIngestionService:
        return get_blob_ingestion_service()


container = ServiceContainer()

provide_auth_settings = container.auth_settings
provide_session_store = container.session_store
provide_casbin_enforcer = container.casbin_enforcer
provide_oauth_state_manager = container.oauth_state_manager
provide_oauth_client = container.oauth_client
provide_login_service = container.login_service
provide_authorization_service = container.authorization_service
provide_conversation_service = container.conversation_service
provide_embedding_service = container.embedding_service
provide_ingestion_service = container.ingestion_service
provide_agent_registry = container.agent_registry
provide_agent_registry_service = container.agent_registry_service
provide_integration_health_service = container.integration_health_service
provide_agent_catalog_service = container.agent_catalog_service
provide_genie_service = container.genie_service
provide_sharepoint_ingestion_service = container.sharepoint_ingestion_service
provide_blob_ingestion_service = container.blob_ingestion_service
