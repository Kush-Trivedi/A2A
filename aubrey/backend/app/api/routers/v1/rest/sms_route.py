"""The SMS channel — Twilio webhooks + the admin surface.

Webhooks are bearer-less and cookie-less: authenticity comes from the
X-Twilio-Signature check (HMAC over the canonical public URL + params).
Twilio ALWAYS gets a 2xx with empty TwiML — replies go out via the REST
API, and the LLM half of an inbound turn runs as a background task so the
webhook never waits on a model.

Admin endpoints (campaigns, consent, outreach, message audit) use the same
session + CSRF auth as the rest of /admin."""

import asyncio

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse, Response

from .....dto.base import ApiEnvelope
from .....dto.sms import (
    CampaignModel,
    ConsentModel,
    ConsentRequest,
    OutreachRequest,
    OutreachResponse,
    OutreachResultModel,
    RegisterCampaignRequest,
    SmsMessageModel,
)
from .....entity.sms import ConsentStatus, SmsCampaignEntity, SmsConsentEntity, SmsMessageEntity
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.sms import (
    SmsCampaignService,
    SmsChannelService,
    SmsConsentService,
    SmsMessageLogService,
    get_sms_campaign_service,
    get_sms_channel_service,
    get_sms_consent_service,
    get_sms_message_log_service,
    get_sms_settings,
    get_twilio_rest_client,
)
from .....utils.common.logger import Logger
from .....utils.errors import ValidationError

logger = Logger(__name__).get_logger()

_ADMIN_OBJ = "/api/v1/admin"
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_INBOUND_PATH = "/api/v1/sms/webhooks/inbound"
_STATUS_PATH = "/api/v1/sms/webhooks/status"

sms_webhook_router = APIRouter(prefix="/sms/webhooks", tags=["SMS Webhooks"])
sms_admin_router = APIRouter(prefix="/admin/sms", tags=["SMS Admin"])

# Keep strong references to background follow-ups (asyncio only weak-refs tasks).
_background_tasks: set[asyncio.Task] = set()


async def _validated_params(request: Request, canonical_path: str) -> dict[str, str] | None:
    """Form params if the Twilio signature checks out, else None. The
    canonical URL prefers twilio.webhook_base_url (what Twilio actually
    signed) over the proxied request URL."""
    params = {key: str(value) for key, value in (await request.form()).items()}
    settings = get_sms_settings()
    url = (
        f"{settings.webhook_base_url}{canonical_path}"
        if settings.webhook_base_url
        else str(request.url)
    )
    signature = request.headers.get("X-Twilio-Signature", "")
    if not get_twilio_rest_client().validate_signature(
        url=url, params=params, signature=signature
    ):
        logger.warning("Twilio webhook signature rejected", extra={"path": canonical_path})
        return None
    return params


@sms_webhook_router.post("/inbound", include_in_schema=False)
async def sms_inbound(request: Request) -> Response:
    params = await _validated_params(request, _INBOUND_PATH)
    if params is None:
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)

    phone = params.get("From", "").strip()
    body = params.get("Body", "")
    twilio_sid = params.get("MessageSid", "").strip()
    if not phone or not twilio_sid:
        return PlainTextResponse("bad request", status_code=status.HTTP_400_BAD_REQUEST)

    def _safe_int(value: str | None) -> int:
        try:
            return int(value or 0)
        except ValueError:
            return 0

    service = get_sms_channel_service()
    try:
        outcome = await service.handle_inbound_fast(
            phone=phone, body=body, twilio_sid=twilio_sid,
            opt_out_type=params.get("OptOutType", ""),
            num_media=_safe_int(params.get("NumMedia")),
            vendor_details={
                "sms_status": params.get("SmsStatus", ""),
                "to_country": params.get("ToCountry", ""),
                "from_country": params.get("FromCountry", ""),
                "account_sid": params.get("AccountSid", ""),
                "to_number": params.get("To", ""),
            },
        )
        if outcome.background and outcome.followup is not None:
            task = asyncio.create_task(service.run_inbound_followup(outcome.followup))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception:  # noqa: BLE001 — Twilio must always get its TwiML
        logger.error("Inbound SMS handling failed", exc_info=True)

    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")


@sms_webhook_router.post("/status", include_in_schema=False)
async def sms_status(request: Request) -> Response:
    params = await _validated_params(request, _STATUS_PATH)
    if params is None:
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)

    twilio_sid = params.get("MessageSid", "").strip()
    message_status = (
        params.get("MessageStatus", "") or params.get("SmsStatus", "")
    ).strip()
    error_code = params.get("ErrorCode", "").strip()  # present only on failures
    error_message = params.get("ErrorMessage", "").strip()
    if twilio_sid and message_status:
        try:
            await get_sms_message_log_service().apply_status_callback(
                twilio_sid=twilio_sid, status=message_status,
                error_code=error_code, error_message=error_message,
            )
        except Exception:  # noqa: BLE001 — never bounce a callback
            logger.error("SMS status callback handling failed", exc_info=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- #
# Admin surface                                                          #
# --------------------------------------------------------------------- #


def _to_campaign(entity: SmsCampaignEntity) -> CampaignModel:
    return CampaignModel(
        id=entity.id, key=entity.key, agent_key=entity.agent_key,
        mode=entity.mode, description=entity.description,
        created_at=entity.created_at, updated_at=entity.updated_at,
    )


def _to_consent(entity: SmsConsentEntity) -> ConsentModel:
    return ConsentModel(
        phone=entity.phone, status=entity.status, source=entity.source,
        keyword=entity.keyword, note=entity.note,
        opted_in_at=entity.opted_in_at, opted_out_at=entity.opted_out_at,
        history=list(entity.history or []),
    )


def _to_message(entity: SmsMessageEntity) -> SmsMessageModel:
    return SmsMessageModel(
        id=entity.id, phone=entity.phone, direction=entity.direction,
        campaign_key=entity.campaign_key, agent_key=entity.agent_key,
        session_id=entity.session_id, twilio_sid=entity.twilio_sid,
        body=entity.body, status=entity.status, error_code=entity.error_code,
        error_explanation=entity.error_explanation,
        error_message=entity.error_message,
        num_segments=entity.num_segments, num_media=entity.num_media,
        opt_out_type=entity.opt_out_type,
        status_history=list(entity.status_history or []),
        vendor_details=dict(entity.vendor_details or {}),
        created_at=entity.created_at,
    )


@sms_admin_router.post(
    "/campaigns",
    response_model=ApiEnvelope[CampaignModel],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_ADMIN_OBJ, "POST"))],
)
async def register_campaign(
    body: RegisterCampaignRequest,
    context: SessionContext = Depends(get_current_context),
    service: SmsCampaignService = Depends(get_sms_campaign_service),
) -> ApiEnvelope[CampaignModel]:
    campaign = await service.register(
        context=context, key=body.key, agent_key=body.agent_key,
        mode=body.mode, description=body.description,
    )
    return ApiEnvelope(data=_to_campaign(campaign), message="Campaign registered.")


@sms_admin_router.get(
    "/campaigns",
    response_model=ApiEnvelope[list[CampaignModel]],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def list_campaigns(
    context: SessionContext = Depends(get_current_context),
    service: SmsCampaignService = Depends(get_sms_campaign_service),
) -> ApiEnvelope[list[CampaignModel]]:
    campaigns = await service.list(tenant_id=context.tenant_id)
    return ApiEnvelope(data=[_to_campaign(c) for c in campaigns])


@sms_admin_router.post(
    "/consent",
    response_model=ApiEnvelope[ConsentModel],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_ADMIN_OBJ, "POST"))],
)
async def record_consent(
    body: ConsentRequest,
    context: SessionContext = Depends(get_current_context),
    service: SmsConsentService = Depends(get_sms_consent_service),
) -> ApiEnvelope[ConsentModel]:
    """Record consent captured elsewhere (signed intake form, portal
    checkbox, verbal-with-reference). TCPA: outreach without a recorded
    opt-in is refused, so this is the first step of every campaign."""
    tenant_id = get_sms_settings().tenant_id
    if body.status == ConsentStatus.OPTED_IN:
        record = await service.record_opt_in(
            tenant_id=tenant_id, phone=body.phone, source="admin", note=body.note
        )
    elif body.status == ConsentStatus.OPTED_OUT:
        record = await service.record_opt_out(
            tenant_id=tenant_id, phone=body.phone, source="admin", note=body.note
        )
    else:
        raise ValidationError("status must be 'opted_in' or 'opted_out'.")
    return ApiEnvelope(data=_to_consent(record), message="Consent recorded.")


@sms_admin_router.get(
    "/consent/{phone}",
    response_model=ApiEnvelope[ConsentModel],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def get_consent(
    phone: str,
    context: SessionContext = Depends(get_current_context),
    service: SmsConsentService = Depends(get_sms_consent_service),
) -> ApiEnvelope[ConsentModel]:
    tenant_id = get_sms_settings().tenant_id
    record = await service._get(tenant_id, phone.strip())  # noqa: SLF001
    if record is None:
        raise ValidationError(
            "No consent record for this number.", details={"phone": phone}
        )
    return ApiEnvelope(data=_to_consent(record))


@sms_admin_router.post(
    "/outreach",
    response_model=ApiEnvelope[OutreachResponse],
    dependencies=[Depends(require_csrf), Depends(require_permission(_ADMIN_OBJ, "POST"))],
)
async def send_outreach(
    body: OutreachRequest,
    context: SessionContext = Depends(get_current_context),
    service: SmsChannelService = Depends(get_sms_channel_service),
) -> ApiEnvelope[OutreachResponse]:
    if not body.recipients:
        raise ValidationError("Outreach needs at least one recipient.")
    outcomes = await service.send_outreach(
        campaign_key=body.campaign_key,
        recipients=[r.model_dump() for r in body.recipients],
    )
    return ApiEnvelope(
        data=OutreachResponse(
            campaign_key=body.campaign_key.strip().lower(),
            results=[
                OutreachResultModel(
                    phone=o.phone, outcome=o.outcome,
                    twilio_sid=o.twilio_sid, detail=o.detail,
                )
                for o in outcomes
            ],
        ),
        message="Outreach batch finished.",
    )


@sms_admin_router.get(
    "/messages",
    response_model=ApiEnvelope[list[SmsMessageModel]],
    dependencies=[Depends(require_permission(_ADMIN_OBJ, "GET"))],
)
async def list_messages(
    phone: str | None = None,
    campaign_key: str | None = None,
    limit: int = 100,
    context: SessionContext = Depends(get_current_context),
    service: SmsMessageLogService = Depends(get_sms_message_log_service),
) -> ApiEnvelope[list[SmsMessageModel]]:
    tenant_id = get_sms_settings().tenant_id
    messages = await service.list_messages(
        tenant_id=tenant_id, phone=phone, campaign_key=campaign_key, limit=limit
    )
    return ApiEnvelope(data=[_to_message(m) for m in messages])
