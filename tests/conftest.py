"""Shared test fixtures: app with a fake Telnyx client, temp data dir."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeTelnyxClient:
    """Records every send_sms call; never touches the network."""

    def __init__(self, bot_number="+16095550127"):
        self.bot_number = bot_number
        self.sent: list[dict] = []  # [{to, text}]

    def send_sms(self, to: str, text: str) -> bool:
        self.sent.append({"to": to, "text": text})
        return True

    def sent_to(self, to: str) -> list[str]:
        return [m["text"] for m in self.sent if m["to"] == to]


@pytest.fixture()
def fake_telnyx():
    return FakeTelnyxClient()


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        telnyx_api_key="test-key",
        telnyx_bot_number="+16095550127",
        william_cell="+16095550199",  # fake William number for tests
        public_base_url="http://test.local",
        telnyx_public_key=None,  # unsigned accepted in most tests
        data_dir=tmp_path / "data",
    )


@pytest.fixture()
def client(settings, fake_telnyx):
    app = create_app(settings=settings, telnyx_client=fake_telnyx)
    with TestClient(app) as test_client:
        yield test_client


LEAD = "+16095551111"
BOT = "+16095550127"


def webhook_payload(sender: str, text: str, event_type: str = "message.received"):
    """A realistic Telnyx v2 inbound SMS webhook body.

    Every call gets fresh event/message UUIDs, as real Telnyx events do
    — the bot dedupes on these, so reusing one id across test steps
    would (correctly) be treated as a retry.
    """
    import uuid

    return {
        "data": {
            "event_type": event_type,
            "id": str(uuid.uuid4()),
            "occurred_at": "2026-11-05T18:40:00.000+00:00",
            "record_type": "event",
            "payload": {
                "id": str(uuid.uuid4()),
                "record_type": "message",
                "direction": "inbound",
                "type": "SMS",
                "text": text,
                "from": {"phone_number": sender, "line_type": "long_code"},
                "to": [{"phone_number": BOT}],
                "media": [],
                "received_at": "2026-11-05T18:40:00.000+00:00",
            },
        },
        "meta": {"attempt": 1, "delivered_to": "http://test.local/webhooks/telnyx/sms"},
    }


def text_bot(client, sender: str, text: str):
    return client.post("/webhooks/telnyx/sms", json=webhook_payload(sender, text))
