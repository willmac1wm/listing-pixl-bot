"""Shared test fixtures for the Listing Pixl voice bot.

All tests run with $0 and no network: Telnyx HTTP calls and SMS sends
are faked, and the webhook is exercised through TestClient.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the `voice` package importable when pytest runs from
# ~/workspace/listing-pixl-bot (or anywhere else).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi.testclient import TestClient

from voice.config import VoiceSettings
from voice.webhooks import create_voice_app

BOT = "+16095550127"
WILLIAM = "+16095550199"  # fake William number for tests
CALLER = "+16095551111"
CCID = "call-control-123"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "{}"

    def json(self):
        return self._payload


class FakeHttpClient:
    """Records every Telnyx Call Control POST; never touches the network."""

    def __init__(self):
        self.calls: list[dict] = []  # [{url, json}]

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json or {}})
        return FakeResponse(200, {"data": {"result": "ok"}})

    def actions(self, action: str) -> list[dict]:
        return [c for c in self.calls if c["url"].endswith(f"/actions/{action}")]


class FakeSmsClient:
    """Records every send_sms call; never touches the network."""

    def __init__(self, bot_number=BOT):
        self.bot_number = bot_number
        self.sent: list[dict] = []  # [{to, text}]

    def send_sms(self, to: str, text: str) -> bool:
        self.sent.append({"to": to, "text": text})
        return True

    def sent_to(self, to: str) -> list[str]:
        return [m["text"] for m in self.sent if m["to"] == to]


@pytest.fixture()
def fake_http():
    return FakeHttpClient()


@pytest.fixture()
def fake_sms():
    return FakeSmsClient()


@pytest.fixture()
def settings(tmp_path):
    return VoiceSettings(
        telnyx_api_key="test-key",
        telnyx_bot_number=BOT,
        william_cell=WILLIAM,
        telnyx_assistant_id="assistant-test-uuid",
        telnyx_public_key=None,  # unsigned accepted in most tests
        use_voice_ai=True,
        public_base_url="http://test.local",
        data_dir=tmp_path / "data",
    )


@pytest.fixture()
def store(tmp_path):
    from voice.store import VoiceStore

    return VoiceStore(tmp_path / "data")


@pytest.fixture()
def client(settings, fake_http, fake_sms, store):
    app = create_voice_app(
        settings=settings, telnyx_http=fake_http, sms_client=fake_sms, store=store
    )
    with TestClient(app) as test_client:
        yield test_client


def voice_payload(event_type: str, payload: dict | None = None):
    """A realistic Telnyx v2 Call Control webhook body."""
    return {
        "data": {
            "event_type": event_type,
            "id": "6a1d5c2e-0000-4000-8000-000000000001",
            "occurred_at": "2026-11-05T18:40:00.000+00:00",
            "record_type": "event",
            "payload": payload or {},
        },
        "meta": {"attempt": 1, "delivered_to": "http://test.local/webhooks/telnyx/voice"},
    }


def initiated_payload(ccid: str = CCID, caller: str = CALLER):
    return voice_payload(
        "call.initiated",
        {
            "call_control_id": ccid,
            "from": {"phone_number": caller},
            "to": {"phone_number": BOT},
        },
    )


LEAD_ARGS = {
    "name": "Sarah",
    "phone": CALLER,
    "property_address": "123 Beach Ave, Cape May",
    "shoot_type": "selling",
    "package_interest": "Standard",
    "ready_when": "next week",
    "message": "3 bed 2 bath, about 2000 sq ft",
    "needs_callback": True,
}
