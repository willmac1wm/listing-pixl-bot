"""IVR fallback — plain Call Control TTS/gather, no Voice AI.

Used when Voice AI is unavailable at launch (USE_VOICE_AI=false, or no
TELNYX_ASSISTANT_ID, or the assistant fails to start). Flow:

    answer -> speak greeting + pricing menu -> gather_using_ai
    (name / address / shoot type / package / ready-when / message)
    -> on gather result: store lead, text William the summary, hang up.

All speech uses Telnyx synthetic text-to-speech (speak action,
voice "female"). No voice clone is used anywhere in this package.
"""

from __future__ import annotations

import logging

from .call_control import TelnyxCallControl
from .leads import format_lead_summary, has_any_detail, normalize_lead
from .sms_sender import TelnyxSmsClient
from .store import VoiceStore

logger = logging.getLogger(__name__)

IVR_GREETING = (
    "Hi, thanks for calling Listing Pixl, real estate photography in "
    "Cape May County, New Jersey. "
    "Our packages start at 200 dollars for Essential, 290 for Standard, "
    "389 for Marketing, 549 for Luxury, and 815 for Signature. "
    "Vacation rental shoots are 800 dollars per property. "
    "Please tell me your name, the property address, whether the home is "
    "for sale or an Airbnb or vacation rental, and when it will be ready, "
    "and Will will call you back."
)

IVR_GATHER_INSTRUCTIONS = (
    "You are collecting a lead for Listing Pixl, a real-estate photography "
    "business. Ask the caller, one or two questions at a time, for: their "
    "name; the property address; whether the photos are for a home they are "
    "selling or an Airbnb / vacation rental; which package interests them; "
    "and when the property will be ready. Keep it short. Never promise a "
    "date or time and never say a shoot is booked. If the caller asks for a "
    "discount, a promised date, payment, or anything outside Cape May "
    "County, note it and offer a callback from Will."
)

# JSON Schema describing what gather_using_ai should collect.
IVR_GATHER_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Caller's name"},
        "property_address": {
            "type": "string",
            "description": "Property address for the shoot",
        },
        "shoot_type": {
            "type": "string",
            "enum": ["selling", "vacation_rental"],
            "description": "selling, or vacation_rental for Airbnb / vacation rental",
        },
        "package_interest": {
            "type": "string",
            "description": "Package the caller is interested in, if any",
        },
        "ready_when": {
            "type": "string",
            "description": "When the property will be ready for photos",
        },
        "message": {
            "type": "string",
            "description": "Anything else the caller said, or a callback message",
        },
        "needs_callback": {
            "type": "boolean",
            "description": "Whether the caller wants Will to call them back",
        },
    },
    "required": ["name", "property_address"],
}


def start_ivr_fallback(
    call_control: TelnyxCallControl, call_control_id: str
) -> None:
    """Answer the call and run the IVR gather flow.

    Each step is best-effort: if a Call Control action fails, log it and
    keep going so the caller is never left on a dead line.
    """
    if not call_control.answer(call_control_id):
        logger.error("IVR fallback: answer failed for %s", call_control_id)
        return
    call_control.speak(call_control_id, IVR_GREETING)
    call_control.gather_using_ai(
        call_control_id,
        parameters=IVR_GATHER_PARAMETERS,
        assistant_instructions=IVR_GATHER_INSTRUCTIONS,
    )


def handle_gather_result(
    result: dict,
    call_control_id: str,
    caller_number: str | None,
    store: VoiceStore,
    sms_client: TelnyxSmsClient,
    william_cell: str,
    call_control: TelnyxCallControl,
) -> dict | None:
    """Store an IVR gather result, text William, and hang up.

    Returns the stored lead, or None when nothing usable was gathered
    (in which case no SMS is sent — no noise for an empty call).
    """
    raw = dict(result or {})
    raw.setdefault("phone", caller_number)
    lead = normalize_lead(raw, source="voice-ivr", call_control_id=call_control_id)
    if not has_any_detail(lead):
        logger.info(
            "IVR gather for %s returned no details; hanging up silently",
            call_control_id,
        )
        call_control.hangup(call_control_id)
        return None

    stored = store.add_lead(lead)
    if not store.summary_sent_for(call_control_id):
        if sms_client.send_sms(william_cell, format_lead_summary(stored)):
            store.mark_summary_sent(call_control_id)
            stored["summary_sent"] = True
    call_control.speak(
        call_control_id,
        "Thanks, I've got that. Will will call you back. Goodbye.",
    )
    call_control.hangup(call_control_id)
    return stored
