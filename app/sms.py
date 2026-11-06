"""Telnyx inbound SMS webhook + conversation engine.

Webhook (Telnyx docs, developers.telnyx.com — verified 2026-11-05):
  POST {PUBLIC_BASE_URL}/webhooks/telnyx/sms
  Headers: telnyx-signature-ed25519 (base64 Ed25519 signature),
           telnyx-timestamp (unix seconds)
  Signed message: "{timestamp}|{raw_json_body}" — verify with the
  account public key (TELNYX_PUBLIC_KEY), 5-minute replay tolerance.
  Body (v2 event):
    {"data": {"event_type": "message.received",
              "id": "<event uuid>", "occurred_at": "<ISO 8601>",
              "record_type": "event",
              "payload": {"record_type": "message", "direction": "inbound",
                          "type": "SMS", "text": "...",
                          "from": {"phone_number": "+1..."},
                          "to": [{"phone_number": "+1..."}],
                          "media": []}}}

The parser is defensive: any missing field degrades to "ignore this
event" (still HTTP 200) instead of crashing. Non-message events
(message.sent / message.finalized / ...) are acknowledged and ignored.

Conversation engine: per-phone-number state machine
  new -> asked-type -> asked-address -> asked-details -> asked-package
  -> lead-complete
Replies are short (1-3 sentences, at most two questions). The bot takes
information; it NEVER promises a date/time or confirms a booking.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from . import pricing
from .config import Settings
from .state import (
    STATE_ASKED_ADDRESS,
    STATE_ASKED_DETAILS,
    STATE_ASKED_PACKAGE,
    STATE_ASKED_TYPE,
    STATE_LEAD_COMPLETE,
    STATE_NEW,
    LeadStore,
    SeenEventStore,
)
from .telnyx_client import TelnyxClient

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sms"])

SIGNATURE_TOLERANCE_SECONDS = 300  # Telnyx replay protection: 5 minutes

# ---------------------------------------------------------------------------
# Webhook signature verification (Ed25519)
# ---------------------------------------------------------------------------


def verify_telnyx_signature(
    raw_body: bytes,
    timestamp: str | None,
    signature_b64: str | None,
    public_key_b64: str | None,
) -> bool:
    """Verify a Telnyx webhook signature.

    Returns True when there is no public key configured (local testing
    only — the caller logs that). When a key IS configured, the
    signature must verify and the timestamp must be within 5 minutes.
    """
    if not public_key_b64:
        logger.warning(
            "TELNYX_PUBLIC_KEY not set — accepting unsigned webhook "
            "(local testing only; set the key before going live)"
        )
        return True
    if not timestamp or not signature_b64:
        logger.warning("Signed webhook expected but signature/timestamp missing")
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > SIGNATURE_TOLERANCE_SECONDS:
        logger.warning("Telnyx webhook timestamp too old/new: %s", timestamp)
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64)
        )
        signed = timestamp.encode("utf-8") + b"|" + raw_body
        public_key.verify(base64.b64decode(signature_b64), signed)
        return True
    except ImportError:
        logger.error("cryptography package missing — cannot verify signature")
        return False
    except (InvalidSignature, ValueError, Exception) as exc:
        logger.warning("Telnyx signature verification failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Inbound payload parsing (defensive)
# ---------------------------------------------------------------------------


def parse_inbound(payload: dict) -> dict | None:
    """Extract {from, to, text, type} from a Telnyx v2 event.

    Returns None for anything that is not a usable inbound message
    (wrong event type, missing sender, missing text) — the caller still
    answers 200 so Telnyx does not retry.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    if data.get("event_type") != "message.received":
        return None
    inner = data.get("payload")
    if not isinstance(inner, dict):
        return None
    sender = inner.get("from") or {}
    from_number = sender.get("phone_number") if isinstance(sender, dict) else None
    text = inner.get("text")
    if not from_number or not isinstance(text, str) or not text.strip():
        return None
    to_list = inner.get("to") or []
    to_number = None
    if isinstance(to_list, list) and to_list:
        first = to_list[0]
        if isinstance(first, dict):
            to_number = first.get("phone_number")
    return {
        "from": str(from_number).strip(),
        "to": to_number,
        "text": text.strip(),
        "type": inner.get("type") or "SMS",
        "message_id": inner.get("id"),
        "event_id": data.get("id"),
    }


# ---------------------------------------------------------------------------
# Intent / content helpers
# ---------------------------------------------------------------------------

STOP_WORDS = {"stop", "unsubscribe", "quit", "end", "stop all", "cancel sms"}
START_WORDS = {"start", "subscribe", "unstop", "yes"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def is_blacklisted(text: str) -> bool:
    lowered = _norm(text)
    return any(term in lowered for term in pricing.BLACKLISTED_TERMS)


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


SELLING_TERMS = ["sell", "selling", "sale", "listing", "listed", "realtor",
                 "real estate agent", "my house", "my home", "our house",
                 "for sale"]
RENTAL_TERMS = ["airbnb", "vrbo", "vacation rental", "vacation home",
                "rental", "rent it", "guests", "short-term rental",
                "short term rental"]

PACKAGE_WORDS = {
    "essential": "essential",
    "standard": "standard",
    "marketing": "marketing",
    "luxury": "luxury",
    "signature": "signature",
}


def extract_name(text: str) -> str | None:
    """Pull a person's name out of a free-text reply, if stated."""
    cleaned = text.strip()
    for pattern in (
        r"(?i)\bmy name is\s+([A-Za-z][A-Za-z'’\- ]{1,40})",
        r"(?i)\bthis is\s+([A-Za-z][A-Za-z'’\- ]{1,40})",
        r"(?i)\bi['’]?m\s+([A-Za-z][A-Za-z'’\- ]{1,40})",
        r"(?i)\bI am\s+([A-Za-z][A-Za-z'’\- ]{1,40})",
    ):
        match = re.search(pattern, cleaned)
        if match:
            name = match.group(1).strip()
            # Cut off anything after a comma / "and" / sentence end.
            name = re.split(r"[,.;]|\band\b", name)[0].strip()
            words = name.split()
            if 1 <= len(words) <= 3:
                return " ".join(w.title() for w in words)
    # Bare name reply: 1-3 words, letters only, no keywords/digits.
    words = cleaned.split()
    if 1 <= len(words) <= 3 and all(w.isalpha() for w in words):
        lowered = _norm(cleaned)
        if not _contains_any(lowered, SELLING_TERMS + RENTAL_TERMS):
            return cleaned.title()
    return None


def detect_property_type(text: str) -> str | None:
    lowered = _norm(text)
    if _contains_any(lowered, RENTAL_TERMS):
        return "vacation_rental"
    if _contains_any(lowered, SELLING_TERMS):
        return "selling"
    return None


def detect_package(text: str) -> str | None:
    lowered = _norm(text)
    for word, key in PACKAGE_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            return key
    return None


def parse_details(text: str) -> dict:
    """Extract beds / baths / sqft from free text; missing keys stay None."""
    lowered = _norm(text)
    out: dict = {}
    bed = re.search(r"(\d+)\s*(?:bed|br\b|bedroom)", lowered)
    bath = re.search(r"(\d+(?:\.\d+)?)\s*(?:bath|ba\b|bathroom)", lowered)
    sqft = re.search(r"([\d,]+)\s*(?:sq\s*ft|sqft|square feet|sf\b)", lowered)
    if bed:
        out["beds"] = int(bed.group(1))
    if bath:
        out["baths"] = float(bath.group(1))
    if sqft:
        try:
            out["sqft"] = int(sqft.group(1).replace(",", ""))
        except ValueError:
            pass
    return out


def is_outside_area(text: str) -> bool:
    lowered = _norm(text)
    return _contains_any(lowered, pricing.OUTSIDE_AREA_TERMS)


def detect_escalation(text: str) -> str | None:
    """Return an escalation flag, or None. Checked before normal flow."""
    lowered = _norm(text)
    if _contains_any(lowered, ["discount", "cheaper", "free shoot",
                                "free photo", "do it for free", "no charge",
                                "custom price", "lower price", "price match",
                                "can you do it for"]):
        return "discount_request"
    if _contains_any(lowered, ["deposit", "payment", "pay you", "how do i pay",
                                "credit card", "cancel my shoot",
                                "cancel the shoot", "refund"]):
        return "payment_or_cancellation"
    if _contains_any(lowered, ["model", "models", "staging", "stager",
                                "extra crew", "videographer", "drone pilot"]):
        return "needs_models_staging_crew"
    if _contains_any(lowered, ["scam", "fraud", "this is a scam",
                                "you're a scam", "ripoff", "rip off"]):
        return "scam_or_angry"
    if _contains_any(lowered, ["this is ridiculous", "terrible",
                                "worst", "angry", "furious", "unacceptable"]):
        return "angry_or_confused"
    return None


def detect_date_promise_request(text: str) -> bool:
    lowered = _norm(text)
    if re.search(
        r"\b(can you|could you|are you free|available|book|schedule|shoot)"
        r"\b.{0,40}\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
        r"|tomorrow|today|next week|\d{1,2}/\d{1,2}|\d{1,2}\s*(am|pm))",
        lowered,
    ):
        return True
    return _contains_any(lowered, ["book me", "book a shoot", "schedule a shoot",
                                   "lock in a date", "confirm a time"])


# ---------------------------------------------------------------------------
# Lead summaries to William
# ---------------------------------------------------------------------------


def format_lead_summary(convo: dict, note: str | None = None) -> str:
    parts = ["Listing Pixl lead:"]
    name = convo.get("name") or "Name not given"
    parts.append(f"{name} {convo.get('phone', '')}")
    if convo.get("address"):
        parts.append(f"Address: {convo['address']}")
    if convo.get("property_type") == "vacation_rental":
        parts.append("Type: Airbnb / vacation rental ($800 shoot)")
    elif convo.get("property_type") == "selling":
        parts.append("Type: selling")
    if convo.get("package_interest"):
        pkg = pricing.PACKAGES.get(convo["package_interest"], {})
        parts.append(
            f"Package: {pkg.get('name', convo['package_interest'])}"
            f" (${pkg.get('price', '?')})"
        )
    details = []
    if convo.get("beds"):
        details.append(f"{convo['beds']}bd")
    if convo.get("baths"):
        baths = convo["baths"]
        details.append(f"{int(baths) if float(baths).is_integer() else baths}ba")
    if convo.get("sqft"):
        details.append(f"{convo['sqft']:,} sq ft")
    if details:
        parts.append("Size: " + " / ".join(details))
    if convo.get("readiness"):
        parts.append(f"Ready: {convo['readiness']}")
    flags = convo.get("flags") or []
    if flags:
        parts.append("Flags: " + ", ".join(flags))
    if note:
        parts.append(note)
    return " | ".join(parts)


def _notify_william(
    client: TelnyxClient, settings: Settings, convo: dict, note: str | None = None
) -> None:
    client.send_sms(settings.william_cell, format_lead_summary(convo, note))


# ---------------------------------------------------------------------------
# Conversation engine
# ---------------------------------------------------------------------------


def handle_inbound_message(
    store: LeadStore,
    client: TelnyxClient,
    settings: Settings,
    sender: str,
    text: str,
) -> str | None:
    """Process one inbound text. Returns the reply text, or None to send nothing."""
    convo = store.get(sender)
    store.add_history(sender, "inbound", text)
    lowered = _norm(text)

    # --- Opt-out handling (always first) ---
    if convo.get("opted_out"):
        if lowered in START_WORDS or lowered.startswith("start"):
            store.update(sender, opted_out=False, state=STATE_ASKED_TYPE)
            return pricing.START_CONFIRMATION
        return None  # never text them again after STOP

    # --- Blacklist: reply with NOTHING, flag to William instead ---
    if is_blacklisted(text):
        store.update(sender, blacklisted=True)
        store.add_flag(sender, "blacklisted_contact")
        _notify_william(
            client, settings, store.get(sender),
            note="BLACKLISTED contact — bot did NOT reply.",
        )
        logger.warning("Blacklisted contact text from %s; no reply sent", sender)
        return None

    # --- STOP / opt-out ---
    if lowered in STOP_WORDS or lowered.startswith("stop"):
        store.update(sender, opted_out=True)
        return pricing.STOP_CONFIRMATION

    # --- Human / talk-to-Will request ---
    if _contains_any(lowered, ["talk to will", "speak to will", "call me",
                                "talk to a human", "talk to someone",
                                "real person", "can i talk", "have will call"]):
        is_new = store.add_flag(sender, "wants_will_callback")
        store.update(sender)
        if is_new:
            _notify_william(
                client, settings, store.get(sender),
                note="Wants Will to call back.",
            )
        return pricing.HUMAN_HANDOFF

    # --- Escalation triggers (flag + tell William, ack to lead) ---
    escalation = detect_escalation(text)
    date_request = detect_date_promise_request(text)
    if escalation or date_request:
        flag = escalation or "date_time_request"
        is_new = store.add_flag(sender, flag)
        if is_new:
            _notify_william(
                client, settings, store.get(sender),
                note=f"Escalation: {flag} — needs Will.",
            )
        if date_request and not escalation:
            return (
                pricing.BOOKING_NO_PROMISE + " "
                + "I've passed your timing along to Will and he'll confirm "
                "a date with you directly."
            )
        return pricing.ESCALATION_ACK

    # --- Informational intents: answer, then resume the current question ---
    def resume_question() -> str:
        return _current_question(store.get(sender))

    if _contains_any(lowered, ["price", "pricing", "how much", "cost",
                                "what do you charge", "rates", "quote"]):
        pkg = detect_package(text)
        answer = pricing.package_detail(pkg) if pkg else pricing.pricing_summary()
        return answer + " " + resume_question()

    if detect_package(text) and convo.get("state") in (STATE_NEW, STATE_LEAD_COMPLETE):
        return pricing.package_detail(detect_package(text)) + " " + resume_question()

    if detect_package(text) and _contains_any(
        lowered, ["include", "package", "what does", "tell me about"]
    ):
        return pricing.package_detail(detect_package(text)) + " " + resume_question()

    if _contains_any(lowered, ["what's included", "what is included",
                                "what do i get", "included in", "include"]):
        pkg = detect_package(text)
        if pkg:
            answer = pricing.package_detail(pkg)
        else:
            answer = (
                "Every package includes professional HDR photos, editing, "
                "next-day delivery, and MLS-ready files. Drone photos, "
                "twilight shots, floor plans, video, and 3D tours come with "
                "the higher packages — want me to walk through them?"
            )
        return answer + " " + resume_question()

    if _contains_any(lowered, ["where do you", "service area", "do you cover",
                                "do you shoot in", "what areas", "location"]):
        answer = (
            "We shoot across Cape May County, NJ — Cape May, the Wildwoods, "
            "Avalon, Stone Harbor, Sea Isle City, Ocean City, and the "
            "townships in between."
        )
        return answer + " " + resume_question()

    if _contains_any(lowered, ["thank", "thanks", "great, thanks"]):
        if convo.get("state") == STATE_LEAD_COMPLETE:
            return pricing.GOODBYE
        return "You're welcome! " + resume_question()

    if _contains_any(lowered, ["bye", "goodbye", "that's all", "that's it"]):
        return pricing.GOODBYE

    # --- State machine ---
    state = convo.get("state") or STATE_NEW

    if state == STATE_NEW:
        store.update(sender, state=STATE_ASKED_TYPE)
        return pricing.GREETING

    if state == STATE_ASKED_TYPE:
        name = extract_name(text)
        ptype = detect_property_type(text)
        updates: dict = {}
        if name:
            updates["name"] = name
        if ptype:
            updates["property_type"] = ptype
        if updates:
            store.update(sender, **updates)
        convo = store.get(sender)
        # Blacklist check on a name the sender just gave.
        if name and is_blacklisted(name):
            store.update(sender, blacklisted=True)
            store.add_flag(sender, "blacklisted_contact")
            _notify_william(
                client, settings, store.get(sender),
                note="BLACKLISTED contact (name given) — bot did NOT reply further.",
            )
            return None
        if not ptype:
            if name:
                return (
                    f"Thanks, {name}! Is this for a home you're selling or "
                    "an Airbnb / vacation rental?"
                )
            return (
                "No problem — is this for a home you're selling or an "
                "Airbnb / vacation rental?"
            )
        store.update(sender, state=STATE_ASKED_ADDRESS)
        convo = store.get(sender)
        reply = pricing.ASK_ADDRESS.format(name=convo.get("name") or "there")
        if ptype == "vacation_rental":
            reply += " " + pricing.VACATION_RENTAL_NOTE
        return reply

    if state == STATE_ASKED_ADDRESS:
        address = text.strip()
        outside = is_outside_area(address)
        updates = {"address": address, "state": STATE_ASKED_DETAILS}
        if outside:
            store.add_flag(sender, "outside_service_area")
        store.update(sender, **updates)
        reply = pricing.ASK_DETAILS.format(address=address)
        if outside:
            reply = pricing.OUTSIDE_AREA_NOTE + " " + reply
        return reply

    if state == STATE_ASKED_DETAILS:
        details = parse_details(text)
        updates = {
            "details_raw": text.strip(),
            "state": STATE_ASKED_PACKAGE,
            **details,
        }
        store.update(sender, **updates)
        convo = store.get(sender)
        recommendation = ""
        pkg = pricing.recommend_package(convo.get("sqft"))
        if pkg:
            recommendation = (
                f"Based on about {convo['sqft']:,} sq ft, the {pkg['name']} "
                f"package (${pkg['price']}) would fit well. "
            )
        elif convo.get("property_type") == "vacation_rental":
            recommendation = pricing.VACATION_RENTAL_NOTE + " "
        return pricing.ASK_PACKAGE.format(recommendation=recommendation)

    if state == STATE_ASKED_PACKAGE:
        convo = store.get(sender)
        if not convo.get("package_interest"):
            pkg_key = detect_package(text)
            if pkg_key:
                store.update(sender, package_interest=pkg_key)
                return pricing.ASK_READINESS.format(
                    package=pricing.PACKAGES[pkg_key]["name"]
                )
            if _contains_any(lowered, ["recommend", "not sure", "which",
                                       "suggest", "help me pick", "what fits"]):
                sqft = convo.get("sqft")
                pkg = pricing.recommend_package(sqft)
                if pkg:
                    store.update(sender, package_interest=pkg["key"])
                    return pricing.ASK_READINESS.format(package=pkg["name"])
                return (
                    "Happy to recommend one — about how many square feet "
                    "is the home?"
                )
            return (
                "No worries — the packages are Essential ($200), Standard "
                "($290), Marketing ($389), Luxury ($549), and Signature "
                "($815). Which one sounds closest, or would you like me "
                "to recommend one based on the size?"
            )
        # Package already chosen: this message is the readiness answer.
        store.update(
            sender,
            readiness=text.strip(),
            state=STATE_LEAD_COMPLETE,
            summary_sent=True,
        )
        _notify_william(client, settings, store.get(sender))
        return pricing.LEAD_COMPLETE

    if state == STATE_LEAD_COMPLETE:
        return (
            "Thanks again — Will has your details and will be in touch "
            "shortly. Anything else you'd like to add, just let me know."
        )

    # Unknown state: reset politely.
    store.update(sender, state=STATE_NEW)
    return pricing.GREETING


def _current_question(convo: dict) -> str:
    """The question for the conversation's current state (resume prompt)."""
    state = convo.get("state") or STATE_NEW
    name = convo.get("name") or "there"
    if state == STATE_NEW:
        return pricing.GREETING
    if state == STATE_ASKED_TYPE:
        return (
            "Is this for a home you're selling or an Airbnb / vacation rental?"
        )
    if state == STATE_ASKED_ADDRESS:
        return pricing.ASK_ADDRESS.format(name=name)
    if state == STATE_ASKED_DETAILS:
        return pricing.ASK_DETAILS.format(address=convo.get("address") or "the property")
    if state == STATE_ASKED_PACKAGE:
        if convo.get("package_interest"):
            return pricing.ASK_READINESS.format(
                package=pricing.PACKAGES[convo["package_interest"]]["name"]
            )
        return pricing.ASK_PACKAGE.format(recommendation="")
    return (
        "Will has your details and will be in touch shortly — anything "
        "else you'd like to add?"
    )


# ---------------------------------------------------------------------------
# Webhook route
# ---------------------------------------------------------------------------


@router.post("/webhooks/telnyx/sms")
async def telnyx_sms_webhook(request: Request, background_tasks: BackgroundTasks):
    settings: Settings = request.app.state.settings
    store: LeadStore = request.app.state.store
    seen: SeenEventStore = request.app.state.seen

    raw_body = await request.body()

    # 1. Signature check (skipped with a warning when no key configured).
    if not verify_telnyx_signature(
        raw_body,
        timestamp=request.headers.get("telnyx-timestamp"),
        signature_b64=request.headers.get("telnyx-signature-ed25519"),
        public_key_b64=settings.telnyx_public_key,
    ):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    # 2. Parse JSON defensively — malformed bodies are acknowledged, not crashed on.
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except (json.JSONDecodeError, ValueError):
        logger.warning("Ignoring non-JSON webhook body")
        return JSONResponse({"ok": True, "ignored": "invalid json"}, status_code=200)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": True, "ignored": "not an object"}, status_code=200)

    # 3. Only message.received with a sender + text gets a reply.
    parsed = parse_inbound(payload)
    if parsed is None:
        return JSONResponse({"ok": True, "ignored": "not an inbound message"}, status_code=200)

    # 4. Dedupe Telnyx retries on the message id (fall back to event id).
    dedupe_id = parsed.get("message_id") or parsed.get("event_id")
    if seen.check_and_record(dedupe_id):
        logger.info("Duplicate Telnyx webhook %s ignored", dedupe_id)
        return JSONResponse({"ok": True, "ignored": "duplicate"}, status_code=200)

    # 5. Hand the (possibly slow) Telnyx send calls to a background task
    # so this endpoint answers 200 inside Telnyx's ~2s retry window.
    # TestClient runs background tasks before returning, so tests still
    # observe the replies synchronously.
    background_tasks.add_task(
        _process_inbound, store, request.app.state.telnyx_client,
        settings, parsed["from"], parsed["text"],
    )
    return JSONResponse({"ok": True}, status_code=200)


def _process_inbound(
    store: LeadStore,
    client: TelnyxClient,
    settings: Settings,
    sender: str,
    text: str,
) -> None:
    """Run the conversation engine and send the reply (background task)."""
    logger.info("Inbound SMS from %s: %r", sender, text[:200])
    try:
        reply = handle_inbound_message(store, client, settings, sender, text)
    except Exception:
        logger.exception("Conversation engine failed for %s", sender)
        return
    if reply:
        store.add_history(sender, "outbound", reply)
        client.send_sms(sender, reply)
