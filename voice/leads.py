"""Shared lead helpers for the voice bot: blacklist, normalisation,
and the concise lead-summary text sent to William.

Blacklist source: the SMS bot's pricing.py BLACKLISTED_TERMS
(Jen Maguire / Jennifer M. Dwyer, anyone at Blue Homes Group,
James W. Sofroney Jr.). Duplicated here on purpose — this package is
standalone and must not import the SMS agent's files.
"""

from __future__ import annotations

# Matched case-insensitively against the caller's name and message.
BLACKLISTED_TERMS = [
    "jen maguire",
    "jennifer maguire",
    "jennifer m. dwyer",
    "jennifer dwyer",
    "jen dwyer",
    "james sofroney",
    "james w. sofroney",
    "sofroney",
    "blue homes",
]


def is_blacklisted(name: str | None, message: str | None = None) -> bool:
    """True if the name/message looks like a do-not-engage contact."""
    haystack = " ".join(
        part for part in (name or "", message or "") if part
    ).lower()
    return any(term in haystack for term in BLACKLISTED_TERMS)


def normalize_lead(raw: dict, source: str, call_control_id: str | None = None) -> dict:
    """Normalise a flat lead payload from any voice source.

    Accepts both ``address`` and ``property_address``; ``shoot_type``
    values ``selling`` / ``vacation_rental`` (also accepts ``airbnb`` /
    ``rental`` as aliases for vacation_rental). Missing fields stay None.
    """
    shoot_type = (raw.get("shoot_type") or raw.get("service") or "").strip().lower()
    if shoot_type in ("airbnb", "rental", "vacation rental", "vacation-rental"):
        shoot_type = "vacation_rental"
    if shoot_type not in ("selling", "vacation_rental"):
        shoot_type = shoot_type or None

    needs_callback = raw.get("needs_callback")
    if isinstance(needs_callback, str):
        needs_callback = needs_callback.strip().lower() in (
            "1", "true", "yes", "y",
        )
    elif needs_callback is None:
        needs_callback = True  # default: Will calls back unless told otherwise

    lead = {
        "source": source,
        "call_control_id": call_control_id,
        "conversation_id": raw.get("conversation_id"),
        "name": (raw.get("name") or raw.get("caller_name") or None),
        "phone": (raw.get("phone") or raw.get("callback_number") or None),
        "property_address": (
            raw.get("property_address") or raw.get("address") or None
        ),
        "shoot_type": shoot_type,
        "package_interest": (raw.get("package_interest") or None),
        "ready_when": (raw.get("ready_when") or raw.get("readiness") or None),
        "message": (raw.get("message") or raw.get("notes") or None),
        "needs_callback": bool(needs_callback),
        "flags": [],
    }
    if is_blacklisted(lead["name"], lead["message"]):
        lead["flags"].append("blacklisted")
    return lead


def has_any_detail(lead: dict) -> bool:
    """True if the lead carries at least one usable detail."""
    return bool(
        lead.get("name")
        or lead.get("phone")
        or lead.get("property_address")
        or lead.get("message")
    )


def format_lead_summary(lead: dict) -> str:
    """One concise SMS for William. Keeps it short — one text, no essay."""
    parts = ["Voice lead"]
    if lead.get("name"):
        parts[0] += f": {lead['name']}"
    if lead.get("phone"):
        parts.append(lead["phone"])
    if lead.get("property_address"):
        parts.append(lead["property_address"])
    if lead.get("shoot_type"):
        parts.append(
            "selling"
            if lead["shoot_type"] == "selling"
            else "Airbnb/vacation rental"
        )
    if lead.get("package_interest"):
        parts.append(f"package: {lead['package_interest']}")
    if lead.get("ready_when"):
        parts.append(f"ready: {lead['ready_when']}")
    if lead.get("message"):
        parts.append(f"msg: {lead['message']}")
    parts.append(
        "callback: yes" if lead.get("needs_callback") else "callback: no"
    )
    text = " — ".join(parts)
    if "blacklisted" in lead.get("flags", []):
        text = "BLACKLISTED — do not engage. " + text
    return text
