from ..security.oauth import (
    EntraOauthClient,
    OAuthStateManager,
    get_oauth_state_manager,
)
from ..security.session import SessionStore, get_session_store
from ..security.settings import AuthSettings, get_auth_settings
from ..services.agents import (
    AgentRegistryService,
    TeamTokenService,
    get_agent_registry_service,
    get_team_token_service,
)
from ..services.authz import AuthzLoginService, get_authz_login_service
from ..services.documents import (
    BlobSourceService,
    ConnectionService,
    FileUploadService,
    SharePointSourceService,
    get_blob_source_service,
    get_connection_service,
    get_file_upload_service,
    get_sharepoint_source_service,
)
from ..services.knowledge import KnowledgeSinkFactory, get_knowledge_sink_factory


class ServiceContainer:
    def auth_settings(self) -> AuthSettings:
        return get_auth_settings()

    def session_store(self) -> SessionStore:
        return get_session_store()

    def oauth_state_manager(self) -> OAuthStateManager:
        return get_oauth_state_manager()

    def oauth_client(self) -> EntraOauthClient:
        return EntraOauthClient(get_auth_settings())

    def login_service(self) -> AuthzLoginService:
        return get_authz_login_service()

    def agent_registry_service(self) -> AgentRegistryService:
        return get_agent_registry_service()

    def team_token_service(self) -> TeamTokenService:
        return get_team_token_service()

    def connection_service(self) -> ConnectionService:
        return get_connection_service()

    def blob_source_service(self) -> BlobSourceService:
        return get_blob_source_service()

    def sharepoint_source_service(self) -> SharePointSourceService:
        return get_sharepoint_source_service()

    def file_upload_service(self) -> FileUploadService:
        return get_file_upload_service()

    def knowledge_sink_factory(self) -> KnowledgeSinkFactory:
        return get_knowledge_sink_factory()


container = ServiceContainer()

provide_auth_settings = container.auth_settings
provide_session_store = container.session_store
provide_oauth_state_manager = container.oauth_state_manager
provide_oauth_client = container.oauth_client
provide_login_service = container.login_service
provide_agent_registry_service = container.agent_registry_service
provide_team_token_service = container.team_token_service
provide_connection_service = container.connection_service
provide_blob_source_service = container.blob_source_service
provide_sharepoint_source_service = container.sharepoint_source_service
provide_file_upload_service = container.file_upload_service
provide_knowledge_sink_factory = container.knowledge_sink_factory
