"""Listing Pixl voice bot — FastAPI router (standalone, importable).

Endpoints (all POST unless noted):
    /webhooks/telnyx/voice       Call Control events from Telnyx.
    /tools/log-lead              Assistant webhook tool (async log_lead).
    /voice/dynamic-variables     Dynamic variables for the assistant.
    /webhooks/telnyx/insights    Conversation insights / ended events.

Wiring:
    - Call Control webhook URL (Telnyx app) -> /webhooks/telnyx/voice.
      Telnyx delivers ALL call events there, including
      call.conversation.created / .ended / .insights.generated, so the
      voice webhook handles those too; /webhooks/telnyx/insights is the
      same handler for setups that point insights at their own URL.
    - The assistant's log_lead tool URL -> /tools/log-lead.
    - The assistant's dynamic_variables_webhook_url ->
      /voice/dynamic-variables.

Signature verification: Ed25519 when TELNYX_PUBLIC_KEY is set (401 on
failure); when unset, unsigned requests are accepted and a warning is
logged (local testing only). Webhooks always answer 200 quickly (Telnyx
retries after ~2 s) except for failed signature verification.

Importable later into the main app: ``from voice.webhooks import router``
(or ``create_voice_app`` for a standalone server). This package does not
import anything from the SMS agent's ``app/`` package.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .call_control import TelnyxCallControl
from .config import VoiceSettings, get_voice_settings
from .ivr_fallback import handle_gather_result, start_ivr_fallback
from .leads import (
    format_lead_summary,
    has_any_detail,
    is_blacklisted,
    normalize_lead,
)
from .signatures import verify_signature
from .sms_sender import TelnyxSmsClient
from .store import VoiceStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

# ---------------------------------------------------------------------------
# Dependency access (injected by create_voice_app; module defaults otherwise)
# ---------------------------------------------------------------------------

_default_settings: VoiceSettings | None = None
_default_store: VoiceStore | None = None
_default_sms: TelnyxSmsClient | None = None
_default_call_control: TelnyxCallControl | None = None


def _deps(request: Request) -> tuple[VoiceSettings, VoiceStore, TelnyxSmsClient, TelnyxCallControl]:
    app = request.app
    settings = getattr(app.state, "voice_settings", None) or _default_settings or get_voice_settings()
    store = getattr(app.state, "voice_store", None) or _default_store or VoiceStore(settings.data_dir)
    sms = getattr(app.state, "voice_sms", None) or _default_sms or TelnyxSmsClient(
        api_key=settings.telnyx_api_key, bot_number=settings.telnyx_bot_number
    )
    cc = getattr(app.state, "voice_call_control", None) or _default_call_control or TelnyxCallControl(
        api_key=settings.telnyx_api_key
    )
    return settings, store, sms, cc


def _get_call_control_id(payload: dict) -> str | None:
    ccid = payload.get("call_control_id")
    return ccid if isinstance(ccid, str) and ccid else None


def _get_caller_number(payload: dict) -> str | None:
    frm = payload.get("from")
    if isinstance(frm, dict):
        number = frm.get("phone_number")
        return number if isinstance(number, str) else None
    if isinstance(frm, str):
        return frm
    return None


async def _read_json_body(request: Request) -> tuple[bytes, dict]:
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        body = {}
    return raw, body if isinstance(body, dict) else {}


def _check_signature(request: Request, raw: bytes, settings: VoiceSettings) -> bool:
    return verify_signature(raw, dict(request.headers), settings.telnyx_public_key)


# ---------------------------------------------------------------------------
# Shared conversation handling (used by both webhook endpoints)
# ---------------------------------------------------------------------------

def _transcript_text(messages: list) -> str:
    lines = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "?")
        content = msg.get("content") or msg.get("text") or ""
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def handle_conversation_created(payload: dict, store: VoiceStore) -> None:
    """call.conversation.created — capture conversation_id <-> call id."""
    ccid = _get_call_control_id(payload)
    conversation_id = payload.get("conversation_id")
    if ccid and conversation_id:
        store.update_call(ccid, conversation_id=conversation_id, status="assistant_started")
        logger.info("Conversation %s started for call %s", conversation_id, ccid)


def handle_conversation_ended(
    payload: dict,
    store: VoiceStore,
    sms: TelnyxSmsClient,
    william_cell: str,
) -> None:
    """call.conversation.ended — store the transcript; text William once."""
    ccid = _get_call_control_id(payload)
    messages = payload.get("messages") or []
    transcript = _transcript_text(messages) if isinstance(messages, list) else ""
    if ccid:
        store.update_call(
            ccid,
            status="ended",
            transcript=transcript or None,
            transcript_messages=messages if isinstance(messages, list) else [],
        )
    if not ccid or store.summary_sent_for(ccid):
        return  # a lead summary was already texted for this call
    call = store.get_call(ccid)
    caller = call.get("caller_number") or ""
    if not transcript and not caller:
        return  # empty / malformed ended event — nothing worth texting
    excerpt = transcript.replace("\n", " ")[:300] if transcript else "no transcript captured"
    text = (
        f"Voice call ended — {caller or 'unknown caller'} — "
        f"{len(messages) if isinstance(messages, list) else 0} turns. {excerpt}"
    )
    if sms.send_sms(william_cell, text):
        store.mark_summary_sent(ccid)


def handle_insights_generated(
    payload: dict,
    store: VoiceStore,
    sms: TelnyxSmsClient,
    william_cell: str,
) -> None:
    """call.conversation_insights.generated — structured lead delivery.

    The structured fields come from the assistant's insight_settings
    schema (name, phone, address, shoot_type, package_interest,
    ready_when, message, needs_callback). Payload shapes vary, so read
    ``insights`` first, then fall back to top-level fields.
    """
    ccid = _get_call_control_id(payload)
    insights = payload.get("insights")
    if not isinstance(insights, dict):
        insights = {
            key: payload[key]
            for key in (
                "name", "phone", "address", "property_address", "shoot_type",
                "package_interest", "ready_when", "message", "needs_callback",
                "summary",
            )
            if key in payload
        }
    if ccid:
        store.update_call(ccid, insights=insights, status="insights_received")
    lead = normalize_lead(
        dict(insights, conversation_id=payload.get("conversation_id")),
        source="voice-insights",
        call_control_id=ccid,
    )
    if ccid:
        call = store.get_call(ccid)
        lead.setdefault("phone", call.get("caller_number"))
        if not lead.get("phone"):
            lead["phone"] = call.get("caller_number")
    if not has_any_detail(lead) and not insights.get("summary"):
        logger.info("Insights for %s carried no lead details; nothing texted", ccid)
        return
    if insights.get("summary") and not lead.get("message"):
        lead["message"] = str(insights["summary"])[:500]
    stored = store.add_lead(lead)
    if ccid and not store.summary_sent_for(ccid):
        if sms.send_sms(william_cell, format_lead_summary(stored)):
            store.mark_summary_sent(ccid)
            stored["summary_sent"] = True


# ---------------------------------------------------------------------------
# POST /webhooks/telnyx/voice — Call Control events
# ---------------------------------------------------------------------------

@router.post("/webhooks/telnyx/voice")
async def telnyx_voice_webhook(request: Request):
    settings, store, sms, cc = _deps(request)
    raw, body = await _read_json_body(request)
    if not _check_signature(request, raw, settings):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    data = body.get("data") or {}
    event_type = data.get("event_type", "")
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ccid = _get_call_control_id(payload)
    logger.info("Voice webhook event=%s call=%s", event_type, ccid)

    try:
        if event_type == "call.initiated" and ccid:
            caller = _get_caller_number(payload)
            called = None
            to = payload.get("to")
            if isinstance(to, dict):
                called = to.get("phone_number")
            store.update_call(
                ccid, status="initiated", caller_number=caller, called_number=called
            )
            if settings.assistant_configured:
                cc.answer_with_assistant(ccid, settings.telnyx_assistant_id)
            else:
                if settings.use_voice_ai:
                    logger.warning(
                        "USE_VOICE_AI is on but TELNYX_ASSISTANT_ID is empty; "
                        "falling back to IVR for call %s",
                        ccid,
                    )
                start_ivr_fallback(cc, ccid)

        elif event_type == "call.answered" and ccid:
            store.update_call(ccid, status="answered")

        elif event_type == "call.hangup" and ccid:
            store.update_call(ccid, status="hangup")

        elif event_type == "call.conversation.created":
            handle_conversation_created(payload, store)

        elif event_type == "call.conversation.ended":
            handle_conversation_ended(payload, store, sms, settings.william_cell)

        elif event_type == "call.conversation_insights.generated":
            handle_insights_generated(payload, store, sms, settings.william_cell)

        elif event_type == "call.conversation.start_failed" and ccid:
            # Assistant couldn't start; the call stays answered. Fall back
            # to a spoken message and hang up rather than leaving silence.
            logger.warning("Assistant failed to start on call %s; IVR fallback", ccid)
            cc.speak(
                ccid,
                "Sorry, our phone assistant isn't available right now. "
                "Please leave your name, property address, and number after "
                "the tone, and Will will call you back.",
            )
            cc.hangup(ccid)

        elif event_type in ("call.ai_gather.ended", "call.gather.ended") and ccid:
            result = payload.get("result") or payload.get("parameters") or {}
            if not isinstance(result, dict):
                result = {}
            if payload.get("digits") and not result:
                result = {"message": f"Menu choice (DTMF): {payload['digits']}"}
            call = store.get_call(ccid)
            handle_gather_result(
                result,
                ccid,
                call.get("caller_number"),
                store,
                sms,
                settings.william_cell,
                cc,
            )

        else:
            logger.info("Voice webhook: unhandled event %s (acknowledged)", event_type)
    except Exception as exc:  # never fail the webhook itself
        logger.exception("Voice webhook handler error for %s: %s", event_type, exc)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /tools/log-lead — assistant webhook tool (async, fire-and-forget)
# ---------------------------------------------------------------------------

@router.post("/tools/log-lead")
async def log_lead_tool(request: Request):
    settings, store, sms, _cc = _deps(request)
    raw, body = await _read_json_body(request)
    if not _check_signature(request, raw, settings):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    # Telnyx posts the flat arguments object as the body.
    args = body if body else {}
    ccid = request.headers.get("x-telnyx-call-control-id")
    lead = normalize_lead(args, source="voice-assistant", call_control_id=ccid)
    if ccid and not lead.get("phone"):
        call = store.get_call(ccid)
        lead["phone"] = call.get("caller_number")
    if not has_any_detail(lead):
        return JSONResponse(
            {"ok": False, "error": "no lead details provided"},
            status_code=400,
        )

    stored = store.add_lead(lead)
    if ccid and store.summary_sent_for(ccid):
        return JSONResponse({"ok": True, "lead_id": stored["id"], "summary_sent": False})
    if sms.send_sms(settings.william_cell, format_lead_summary(stored)):
        if ccid:
            store.mark_summary_sent(ccid)
        stored["summary_sent"] = True
        return JSONResponse({"ok": True, "lead_id": stored["id"], "summary_sent": True})
    logger.warning("Lead %s stored but summary SMS failed to send", stored["id"])
    return JSONResponse({"ok": True, "lead_id": stored["id"], "summary_sent": False})


# ---------------------------------------------------------------------------
# POST /voice/dynamic-variables — variables for {{...}} in instructions
# ---------------------------------------------------------------------------

@router.post("/voice/dynamic-variables")
async def dynamic_variables(request: Request):
    settings, _store, _sms, _cc = _deps(request)
    raw, _body = await _read_json_body(request)
    if not _check_signature(request, raw, settings):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)
    return JSONResponse(
        {
            "dynamic_variables": {
                "business_name": "Listing Pixl",
                "website": "listingpixl.com",
                "email": "ListingPixl.Media@gmail.com",
                "service_area": "Cape May County, NJ",
                "phone": settings.telnyx_bot_number,
                "packages": (
                    "Essential $200, Standard $290, Marketing $389, "
                    "Luxury $549, Signature $815"
                ),
                "minimum_shoot": "$250 per shoot ($200 each for same-location paired shoots)",
                "vacation_rental_shoot": "$800 per property",
            }
        }
    )


# ---------------------------------------------------------------------------
# POST /webhooks/telnyx/insights — insights / ended events (own URL)
# ---------------------------------------------------------------------------

@router.post("/webhooks/telnyx/insights")
async def telnyx_insights_webhook(request: Request):
    settings, store, sms, _cc = _deps(request)
    raw, body = await _read_json_body(request)
    if not _check_signature(request, raw, settings):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    data = body.get("data") or {}
    event_type = data.get("event_type", "")
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    logger.info("Insights webhook event=%s", event_type)
    try:
        if event_type == "call.conversation_insights.generated":
            handle_insights_generated(payload, store, sms, settings.william_cell)
        elif event_type == "call.conversation.ended":
            handle_conversation_ended(payload, store, sms, settings.william_cell)
        elif event_type == "call.conversation.created":
            handle_conversation_created(payload, store)
        else:
            logger.info("Insights webhook: unhandled event %s (acknowledged)", event_type)
    except Exception as exc:
        logger.exception("Insights webhook handler error for %s: %s", event_type, exc)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# App factory (standalone server; the router above is importable on its own)
# ---------------------------------------------------------------------------

def create_voice_app(
    settings: VoiceSettings | None = None,
    telnyx_http: Any | None = None,
    sms_client: TelnyxSmsClient | None = None,
    store: VoiceStore | None = None,
) -> FastAPI:
    """Build a standalone FastAPI app serving the voice bot.

    Tests inject ``telnyx_http`` (fake) and ``sms_client`` (fake).
    """
    settings = settings or get_voice_settings()
    logging.basicConfig(level=settings.log_level)
    store = store or VoiceStore(settings.data_dir)
    sms_client = sms_client or TelnyxSmsClient(
        api_key=settings.telnyx_api_key, bot_number=settings.telnyx_bot_number
    )
    call_control = TelnyxCallControl(
        api_key=settings.telnyx_api_key, http_client=telnyx_http
    )

    app = FastAPI(
        title="Listing Pixl Voice Bot",
        description=(
            "Inbound voice auto-reply + lead capture for Listing Pixl "
            "(Cape May County, NJ real-estate photography) via Telnyx "
            "Voice AI Assistants, with a plain Call Control IVR fallback."
        ),
        version="0.1.0",
    )
    app.state.voice_settings = settings
    app.state.voice_store = store
    app.state.voice_sms = sms_client
    app.state.voice_call_control = call_control

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "service": "listing-pixl-voice-bot",
            "voice_ai_enabled": settings.use_voice_ai,
            "assistant_configured": bool(settings.telnyx_assistant_id),
            "telnyx_configured": bool(
                settings.telnyx_api_key and settings.telnyx_bot_number
            ),
            "signature_verification": bool(settings.telnyx_public_key),
            "data_dir": str(settings.data_dir),
        }

    app.include_router(router)
    return app


# Default app instance for `uvicorn voice.webhooks:app`.
app = create_voice_app()
