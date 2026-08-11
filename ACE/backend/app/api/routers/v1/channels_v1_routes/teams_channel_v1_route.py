import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .....services.teams import get_teams_channel_service
from .....utils.common.logger import Logger

logger = Logger(__name__).get_logger()

teams_channel_v1_router = APIRouter(prefix="/channels/teams", tags=["Channels / Teams"])


async def _handle(request: Request, agent_key: str | None) -> JSONResponse:
    raw_body = await request.body()
    service = get_teams_channel_service()

    binding = await service.binding_for(agent_key)
    if not binding.enabled:
        logger.warning("Teams webhook for non-opted-in agent", extra={"agent_key": agent_key})
        return JSONResponse(
            {"type": "message", "text": "This agent is not enabled for Microsoft Teams."},
            status_code=404,
        )
    if not service.validate_signature(
        raw_body=raw_body,
        authorization=request.headers.get("Authorization", ""),
        secret=binding.secret,
    ):
        logger.warning("Teams webhook HMAC rejected", extra={"agent_key": agent_key})
        return JSONResponse({"type": "message", "text": "Unauthorized."}, status_code=401)

    try:
        activity = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"type": "message", "text": "Bad request."}, status_code=400)

    try:
        reply = await service.handle_activity(activity, agent_key=agent_key)
        text = reply.text
    except Exception:  # noqa: BLE001 — Teams must always get a well-formed reply
        logger.error("Teams activity handling failed", exc_info=True)
        text = "Something went wrong handling that — please try again."

    # Teams outgoing webhooks expect the answer inline in the HTTP response.
    return JSONResponse({"type": "message", "text": text})


@teams_channel_v1_router.post("/{agent_key}/messages", include_in_schema=False)
async def teams_agent_messages(agent_key: str, request: Request) -> JSONResponse:
    """Per-agent webhook — only for agents whose team opted in via
    `data.channels.teams.enabled` in their manifest. The team's Teams Outgoing
    Webhook points here (e.g. /channels/teams/benefits/messages) with THEIR
    secret; conversations bind to that team's agent. Non-opted-in agents 404."""
    return await _handle(request, agent_key)
