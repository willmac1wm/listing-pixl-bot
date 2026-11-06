"""JSON-file lead / conversation store.

No database — one JSON file under the configured data directory.
Writes are atomic (temp file + rename) and guarded by a lock so a
webhook retry cannot interleave a half-written file.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Conversation states, in order.
STATE_NEW = "new"
STATE_ASKED_TYPE = "asked-type"
STATE_ASKED_ADDRESS = "asked-address"
STATE_ASKED_DETAILS = "asked-details"
STATE_ASKED_PACKAGE = "asked-package"
STATE_LEAD_COMPLETE = "lead-complete"

ALL_STATES = [
    STATE_NEW,
    STATE_ASKED_TYPE,
    STATE_ASKED_ADDRESS,
    STATE_ASKED_DETAILS,
    STATE_ASKED_PACKAGE,
    STATE_LEAD_COMPLETE,
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_conversation(phone: str) -> dict:
    return {
        "phone": phone,
        "state": STATE_NEW,
        "name": None,
        "property_type": None,  # "selling" | "vacation_rental" | None
        "address": None,
        "beds": None,
        "baths": None,
        "sqft": None,
        "details_raw": None,
        "package_interest": None,
        "readiness": None,
        "flags": [],  # escalation flags, e.g. "discount_request"
        "opted_out": False,
        "blacklisted": False,
        "summary_sent": False,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
        "history": [],  # [{direction, text, at}]
    }


class LeadStore:
    """Thread-safe JSON store keyed by sender phone number (E.164)."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "leads.json"
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save_all({})

    def _load_all(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Lead store unreadable (%s); starting fresh", exc)
            return {}

    def _save_all(self, data: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self.path)

    def get(self, phone: str) -> dict:
        """Return the conversation for a phone number, creating it."""
        with self._lock:
            data = self._load_all()
            convo = data.get(phone)
            if convo is None:
                convo = new_conversation(phone)
                data[phone] = convo
                self._save_all(data)
            return convo

    def update(self, phone: str, **fields) -> dict:
        """Set fields on a conversation and persist it."""
        with self._lock:
            data = self._load_all()
            convo = data.get(phone) or new_conversation(phone)
            convo.update(fields)
            convo["updated_at"] = _utcnow()
            data[phone] = convo
            self._save_all(data)
            return convo

    def add_history(self, phone: str, direction: str, text: str) -> None:
        with self._lock:
            data = self._load_all()
            convo = data.get(phone) or new_conversation(phone)
            convo.setdefault("history", []).append(
                {"direction": direction, "text": text, "at": _utcnow()}
            )
            # Keep the log bounded.
            convo["history"] = convo["history"][-50:]
            convo["updated_at"] = _utcnow()
            data[phone] = convo
            self._save_all(data)

    def add_flag(self, phone: str, flag: str) -> bool:
        """Add an escalation flag. Returns True if it was new."""
        with self._lock:
            data = self._load_all()
            convo = data.get(phone) or new_conversation(phone)
            flags = convo.setdefault("flags", [])
            is_new = flag not in flags
            if is_new:
                flags.append(flag)
            convo["updated_at"] = _utcnow()
            data[phone] = convo
            self._save_all(data)
            return is_new

    def all(self) -> dict:
        with self._lock:
            return self._load_all()


class SeenEventStore:
    """Dedupe for Telnyx webhook retries (meta.attempt 1-3).

    Telnyx retries a webhook if our endpoint does not answer 2xx within
    ~2 seconds. Without dedupe, a retry would re-run the conversation
    engine and could double-send a reply or a lead summary to William.
    Stores the last 500 event/message ids in data/seen_ids.json.
    """

    MAX_KEPT = 500

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "seen_ids.json"
        self._lock = threading.Lock()

    def check_and_record(self, event_id: str | None) -> bool:
        """Return True if this id was already seen (duplicate)."""
        if not event_id:
            return False
        with self._lock:
            try:
                ids = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(ids, list):
                    ids = []
            except (OSError, json.JSONDecodeError):
                ids = []
            if event_id in ids:
                return True
            ids.append(event_id)
            self.path.write_text(
                json.dumps(ids[-self.MAX_KEPT:]), encoding="utf-8"
            )
            return False
