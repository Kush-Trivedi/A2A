from pydantic import Field
from datetime import datetime
from ..base import StrictBaseModel

class AuthModeResponse(StrictBaseModel):
    environment: str
    mode: str = Field(..., description="entra_oauth | anonymous")
    oauth_enabled: bool
    jwt_auth_enabled: bool
    rbac_enabled: bool
    login_url: str | None = None
    logout_url: str | None = None

class UserProfileResponse(StrictBaseModel):
    tenant_id: str
    user_id: str
    actor_id: str
    email: str
    display_name: str = ""
    auth_provider: str = "entra"
    roles: list[str] = Field(default_factory=list)

class MeResponse(StrictBaseModel):
    user: UserProfileResponse
    csrf_token: str | None = None

class LoginResultResponse(StrictBaseModel):
    user: UserProfileResponse
    is_new_login: bool
    granted_roles: list[str] = Field(default_factory=list)
    revoked_roles: list[str] = Field(default_factory=list)
    session_expires_at: datetime