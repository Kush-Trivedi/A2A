from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from .....config.application_context import get_application_context
from .....services.sms import get_sms_channel_service, get_twilio_sms_client, get_twilio_settings
from .....utils.common.logger import Logger

logger = Logger(__name__).get_logger()

sms_channel_v1_router = APIRouter(prefix="/channels/sms", tags=["Channels / SMS"])

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_INBOUND_PATH = "/api/v1/channels/sms/inbound"


@sms_channel_v1_router.post("/inbound", include_in_schema=False)
async def sms_inbound(request: Request) -> PlainTextResponse:
    params = {key: str(value) for key, value in (await request.form()).items()}
    settings = get_twilio_settings()

    canonical_url = f"{settings.webhook_base_url}{_INBOUND_PATH}"
    signature = request.headers.get("X-Twilio-Signature", "")
    if not get_twilio_sms_client().validate_signature(
        url=canonical_url, params=params, signature=signature
    ):
        logger.warning("Twilio webhook signature rejected")
        return PlainTextResponse("forbidden", status_code=403)

    from_number = params.get("From", "")
    to_number = params.get("To", "")
    body = params.get("Body", "")
    message_sid = params.get("MessageSid", "")
    if not from_number or not message_sid:
        return PlainTextResponse("bad request", status_code=400)

    tenant_id = get_application_context().twilio.get("tenant_id") or "default"
    try:
        result = await get_sms_channel_service().handle_inbound(
            tenant_id=str(tenant_id),
            from_number=from_number,
            to_number=to_number,
            body=body,
            message_sid=message_sid,
        )
        logger.info(
            "Inbound SMS handled",
            extra={"conversation_id": result.conversation_id, "status": result.status},
        )
    except Exception:  # noqa: BLE001 — Twilio must always get 200/TwiML
        logger.error("Inbound SMS handling failed", exc_info=True)

    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")


_STATUS_PATH = "/api/v1/channels/sms/status"


@sms_channel_v1_router.post("/status", include_in_schema=False)
async def sms_status(request: Request) -> PlainTextResponse:
    params = {key: str(value) for key, value in (await request.form()).items()}
    settings = get_twilio_settings()
    if not get_twilio_sms_client().validate_signature(
        url=f"{settings.webhook_base_url}{_STATUS_PATH}",
        params=params,
        signature=request.headers.get("X-Twilio-Signature", ""),
    ):
        return PlainTextResponse("forbidden", status_code=403)

    message_sid = params.get("MessageSid", "")
    status = params.get("MessageStatus", params.get("SmsStatus", ""))
    await get_sms_channel_service().update_delivery_status(
        message_sid=message_sid, status=status
    )
    logger.info(
        "SMS delivery status", extra={"message_sid": message_sid, "status": status}
    )
    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")
