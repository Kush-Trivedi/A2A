from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from .....config.application_context import get_application_context
from .....dto.auth import AuthModeResponse, MeResponse, UserProfileResponse
from .....dto.base import ApiEnvelope
from .....security.dependencies import get_current_context, get_optional_context
from .....security.identity import (
    IdentityClaimDiagnostics,
    IdentityProfileEnricher,
    JWTValidator,
    get_jwt_validator,
)
from .....security.oauth import EntraOauthClient, OAuthState, OAuthStateManager
from .....security.session import SessionContext, SessionCookieManager, SessionStore
from .....security.settings import AuthSettings
from .....services.authz import AuthzLoginService, LoginProvisioning
from .....utils.common.logger import Logger
from .....utils.errors import (
    ExternalServiceError,
    InvalidTokenError,
    NotFoundError,
    OAuthStateError,
)
from ....dependencies import (
    provide_auth_settings,
    provide_login_service,
    provide_oauth_client,
    provide_oauth_state_manager,
    provide_session_store,
)

logger = Logger(__name__).get_logger()

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")


def _safe_return_to(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        return fallback
    fb = urlparse(fallback)
    if fb.scheme and fb.netloc:
        return f"{fb.scheme}://{fb.netloc}{value}"
    return value


def _require_oauth(settings: AuthSettings) -> None:
    if not settings.oauth_enabled:
        raise NotFoundError("Entra OAuth is not enabled in this environment.")


def _to_profile(context: SessionContext) -> UserProfileResponse:
    return UserProfileResponse(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        actor_id=context.actor_id,
        email=context.email,
        display_name=context.display_name,
        auth_provider=context.auth_provider,
        roles=list(context.roles),
    )


@auth_router.get("/mode", response_model=ApiEnvelope[AuthModeResponse])
async def auth_mode(
    settings: AuthSettings = Depends(provide_auth_settings),
) -> ApiEnvelope[AuthModeResponse]:
    mode = "entra_oauth" if settings.oauth_enabled else "anonymous"
    return ApiEnvelope(
        data=AuthModeResponse(
            environment=get_application_context().environment,
            mode=mode,
            oauth_enabled=settings.oauth_enabled,
            jwt_auth_enabled=settings.jwt_auth_enabled,
            rbac_enabled=settings.rbac_enabled,
            login_url="/api/v1/auth/login" if settings.oauth_enabled else None,
            logout_url="/api/v1/auth/logout" if settings.oauth_enabled else None,
        )
    )


@auth_router.get("/login", name="auth_login")
async def auth_login(
    return_to: str | None = None,
    settings: AuthSettings = Depends(provide_auth_settings),
    oauth: EntraOauthClient = Depends(provide_oauth_client),
    state_mgr: OAuthStateManager = Depends(provide_oauth_state_manager),
) -> RedirectResponse:
    _require_oauth(settings)

    safe_return = _safe_return_to(return_to, settings.post_login_redirect_uri)
    authorize = oauth.build_authorize_request(return_to=safe_return)

    signed = state_mgr.pack(
        OAuthState(
            state=authorize.state,
            code_verifier=authorize.code_verifier,
            nonce=authorize.nonce,
            return_to=safe_return,
        )
    )

    response = RedirectResponse(url=authorize.url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=state_mgr.cookie_name,
        value=signed,
        max_age=state_mgr.max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )

    return response


@auth_router.get("/callback", name="oauth_callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    settings: AuthSettings = Depends(provide_auth_settings),
    oauth: EntraOauthClient = Depends(provide_oauth_client),
    state_mgr: OAuthStateManager = Depends(provide_oauth_state_manager),
    login_service: AuthzLoginService = Depends(provide_login_service),
    store: SessionStore = Depends(provide_session_store),
    validator: JWTValidator = Depends(get_jwt_validator),
) -> RedirectResponse:
    _require_oauth(settings)

    if error:
        logger.error("OAuth provider error", extra={"error": error, "description": error_description})
        raise OAuthStateError(f"OAuth error: {error}")

    if not code or not state:
        raise OAuthStateError("Missing code/state in callback.")

    signed = request.cookies.get(state_mgr.cookie_name)
    oauth_state = state_mgr.unpack(signed) if signed else None
    if oauth_state is None:
        raise OAuthStateError("Missing or invalid OAuth state.")

    if oauth_state.state != state:
        raise OAuthStateError("OAuth state mismatch.")

    try:
        tokens = await oauth.exchange_code(code=code, code_verifier=oauth_state.code_verifier)
    except Exception as exc:
        logger.error("Token exchange failed", extra={"error": str(exc)})
        raise ExternalServiceError("Token exchange failed.", cause=exc) from exc

    if not tokens.id_token:
        raise ExternalServiceError("No id_token returned.")

    try:
        identity = await validator.validate(tokens.id_token)
    except Exception as exc:
        logger.error("ID token validation failed", extra={"error": str(exc)})
        raise InvalidTokenError("ID token validation failed.", cause=exc) from exc

    IdentityClaimDiagnostics.log(identity)
    try:
        graph_profile = await oauth.fetch_user_profile(tokens.access_token)
    except Exception as exc:
        logger.warning(
            "Microsoft Graph profile lookup failed; using validated ID-token claims.",
            extra={"error_type": type(exc).__name__},
        )
        graph_profile = None
    identity = IdentityProfileEnricher.enrich(identity, graph_profile)

    provisioning: LoginProvisioning = await login_service.provision_on_login(identity)

    context = await store.create(
        tenant_id=provisioning.tenant_id,
        user_id=provisioning.user_id,
        actor_id=provisioning.actor_id,
        email=provisioning.email,
        display_name=provisioning.display_name,
        roles=provisioning.roles,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
        user_profile=provisioning.authorization_attributes,
    )

    safe_return = _safe_return_to(oauth_state.return_to, settings.post_login_redirect_uri)
    redirect = RedirectResponse(url=safe_return, status_code=status.HTTP_302_FOUND)
    cookies = SessionCookieManager(settings)
    cookies.set(redirect, context.session_id)
    cookies.set_csrf(redirect, context.csrf_token)
    redirect.delete_cookie(state_mgr.cookie_name, path="/")
    return redirect


oauth_compact_router = APIRouter(tags=["Auth"])
oauth_compact_router.add_api_route(
    "/api/auth/callback/microsoft",
    oauth_callback,
    methods=["GET"],
    name="auth_callback_microsoft",
)


@auth_router.get("/me", name="auth_me", response_model=ApiEnvelope[MeResponse])
async def me(
    context: SessionContext = Depends(get_current_context),
) -> ApiEnvelope[MeResponse]:
    # csrf_token is returned so Swagger users can paste it into Authorize —
    # log in via the browser, call /me here, copy the token, done.
    return ApiEnvelope(
        data=MeResponse(user=_to_profile(context), csrf_token=context.csrf_token)
    )


@auth_router.post("/logout", name="auth_logout", response_model=ApiEnvelope[dict])
async def logout(
    response: Response,
    context: SessionContext = Depends(get_optional_context),
    settings: AuthSettings = Depends(provide_auth_settings),
    store: SessionStore = Depends(provide_session_store),
) -> ApiEnvelope[dict]:
    if context is not None:
        await store.delete(context.session_id)
    SessionCookieManager(settings).clear(response)
    return ApiEnvelope(data={"logged_out": True})
