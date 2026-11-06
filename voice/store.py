"""Voice call / lead store.

Standalone JSON-file store for the voice bot (separate file from the SMS
bot's leads.json so the two bots never clobber each other). One file holds
call records keyed by call_control_id plus the list of captured leads.
Writes are atomic (temp file + rename) and guarded by a lock.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_call_record(call_control_id: str) -> dict:
    return {
        "call_control_id": call_control_id,
        "conversation_id": None,
        "caller_number": None,
        "called_number": None,
        "status": "initiated",
        "transcript": None,
        "transcript_messages": [],
        "insights": {},
        "summary_sent": False,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }


class VoiceStore:
    """Thread-safe JSON store for voice calls and leads."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "voice_calls.json"
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save_all({"calls": {}, "leads": []})

    def _load_all(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("calls", {})
                data.setdefault("leads", [])
                return data
            return {"calls": {}, "leads": []}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Voice store unreadable (%s); starting fresh", exc)
            return {"calls": {}, "leads": []}

    def _save_all(self, data: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self.path)

    # -- calls ---------------------------------------------------------------

    def get_call(self, call_control_id: str) -> dict:
        with self._lock:
            data = self._load_all()
            call = data["calls"].get(call_control_id)
            if call is None:
                call = new_call_record(call_control_id)
                data["calls"][call_control_id] = call
                self._save_all(data)
            return call

    def update_call(self, call_control_id: str, **fields) -> dict:
        with self._lock:
            data = self._load_all()
            call = data["calls"].get(call_control_id) or new_call_record(
                call_control_id
            )
            call.update(fields)
            call["updated_at"] = _utcnow()
            data["calls"][call_control_id] = call
            self._save_all(data)
            return call

    def summary_sent_for(self, call_control_id: str) -> bool:
        with self._lock:
            data = self._load_all()
            call = data["calls"].get(call_control_id)
            return bool(call and call.get("summary_sent"))

    def mark_summary_sent(self, call_control_id: str) -> None:
        self.update_call(call_control_id, summary_sent=True)

    # -- leads ---------------------------------------------------------------

    def add_lead(self, lead: dict) -> dict:
        """Persist a lead; returns the stored record (with id + timestamp)."""
        record = dict(lead)
        record.setdefault("id", uuid.uuid4().hex[:12])
        record.setdefault("created_at", _utcnow())
        record.setdefault("flags", [])
        record.setdefault("summary_sent", False)
        with self._lock:
            data = self._load_all()
            data["leads"].append(record)
            self._save_all(data)
        return record

    def all_leads(self) -> list[dict]:
        with self._lock:
            return list(self._load_all()["leads"])

    def all_calls(self) -> dict:
        with self._lock:
            return dict(self._load_all()["calls"])
