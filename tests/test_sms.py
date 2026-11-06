"""Tests for the Listing Pixl SMS bot.

All tests run with $0 and no network: the Telnyx send client is faked
(see conftest.py) and the webhook is exercised through TestClient.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import BOT, LEAD, FakeTelnyxClient, text_bot, webhook_payload

WILLIAM = "+16095550199"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_greeting_then_pricing_answer(client, fake_telnyx):
    # Greeting: bot asks name + selling vs rental.
    resp = text_bot(client, LEAD, "Hi")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert len(replies) == 1
    assert "Listing Pixl" in replies[0]
    assert "selling" in replies[0]

    # Pricing question mid-flow: price list + resume question, no state loss.
    resp = text_bot(client, LEAD, "What are your prices?")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert len(replies) == 2
    price_text = replies[1]
    assert "Essential $200" in price_text
    assert "Standard $290" in price_text
    assert "Marketing $389" in price_text
    assert "Luxury $549" in price_text
    assert "Signature $815" in price_text
    # Retired prices must never appear.
    assert "$295" not in price_text
    assert "Showcase" not in price_text
    assert "Premiere" not in price_text


def test_full_lead_capture_flow(client, fake_telnyx):
    assert text_bot(client, LEAD, "Hi").status_code == 200
    assert text_bot(client, LEAD, "I'm Sarah, selling my house").status_code == 200
    assert text_bot(client, LEAD, "123 Beach Ave, Cape May").status_code == 200
    assert text_bot(client, LEAD, "3 bed 2 bath 2000 sq ft").status_code == 200

    # Package recommendation: 2000 sq ft -> Standard ($290).
    replies = fake_telnyx.sent_to(LEAD)
    assert any("Standard" in r and "$290" in r for r in replies)

    assert text_bot(client, LEAD, "Standard").status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("ready to shoot" in r for r in replies)

    assert text_bot(client, LEAD, "Ready next week").status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("Thank you" in r for r in replies)

    # Lead summary queued to William (fake number) — one concise message.
    summaries = fake_telnyx.sent_to(WILLIAM)
    assert len(summaries) == 1
    summary = summaries[0]
    assert "Sarah" in summary
    assert LEAD in summary
    assert "123 Beach Ave, Cape May" in summary
    assert "selling" in summary
    assert "Standard" in summary
    assert "Ready next week" in summary or "next week" in summary
    assert "3bd" in summary and "2ba" in summary
    assert "2,000 sq ft" in summary

    # Stored state is lead-complete.
    store = client.app.state.store
    convo = store.get(LEAD)
    assert convo["state"] == "lead-complete"
    assert convo["name"] == "Sarah"
    assert convo["package_interest"] == "standard"
    assert convo["summary_sent"] is True


def test_vacation_rental_flow_mentions_800(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    resp = text_bot(client, LEAD, "Hi, I'm Mike, I have an Airbnb in Wildwood Crest")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("$800" in r for r in replies)
    convo = client.app.state.store.get(LEAD)
    assert convo["property_type"] == "vacation_rental"


def test_stop_handling(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    before = len(fake_telnyx.sent)
    resp = text_bot(client, LEAD, "STOP")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert replies[-1].startswith("You're all set")
    assert "won't text you again" in replies[-1]

    # After STOP: no further replies, ever (except the confirmation above).
    count_after_stop = len(fake_telnyx.sent)
    resp = text_bot(client, LEAD, "Hello? Are you there?")
    assert resp.status_code == 200
    assert len(fake_telnyx.sent) == count_after_stop

    # START re-opts in.
    resp = text_bot(client, LEAD, "START")
    assert resp.status_code == 200
    assert len(fake_telnyx.sent) == count_after_stop + 1


def test_blacklist_name_no_reply_but_flagged_to_william(client, fake_telnyx):
    resp = text_bot(client, LEAD, "Hi, this is Jen Maguire, I need photos")
    assert resp.status_code == 200
    # Bot replies with NOTHING to the blacklisted contact.
    assert fake_telnyx.sent_to(LEAD) == []
    # But William gets a flag.
    summaries = fake_telnyx.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert "BLACKLISTED" in summaries[0]

    # Same for James Sofroney Jr. identifying himself mid-conversation.
    other = "+16095552222"
    text_bot(client, other, "Hi")
    fake_telnyx.sent.clear()
    resp = text_bot(client, other, "My name is James Sofroney")
    assert resp.status_code == 200
    assert fake_telnyx.sent_to(other) == []
    assert any("BLACKLISTED" in s for s in fake_telnyx.sent_to(WILLIAM))


def test_malformed_payloads_no_crash_200(client, fake_telnyx):
    # Empty object.
    assert client.post("/webhooks/telnyx/sms", json={}).status_code == 200
    # Non-message event (delivery receipt) — acknowledged, ignored.
    resp = client.post(
        "/webhooks/telnyx/sms", json=webhook_payload(LEAD, "x", "message.sent")
    )
    assert resp.status_code == 200
    assert fake_telnyx.sent_to(LEAD) == []
    # Missing sender / missing text.
    assert client.post(
        "/webhooks/telnyx/sms",
        json={"data": {"event_type": "message.received", "payload": {}}},
    ).status_code == 200
    assert client.post(
        "/webhooks/telnyx/sms",
        json={"data": {"event_type": "message.received",
                       "payload": {"from": {"phone_number": LEAD}}}},
    ).status_code == 200
    # Invalid JSON body.
    resp = client.post(
        "/webhooks/telnyx/sms",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    # No replies sent for any of the malformed inputs.
    assert fake_telnyx.sent_to(LEAD) == []


def test_outside_service_area_flagged(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    text_bot(client, LEAD, "I'm Dana, selling my house")
    resp = text_bot(client, LEAD, "456 Main St, Atlantic City")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("Cape May County" in r for r in replies)
    convo = client.app.state.store.get(LEAD)
    assert "outside_service_area" in convo["flags"]


def test_talk_to_will_handoff(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    resp = text_bot(client, LEAD, "Can I talk to Will please?")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("call you back" in r for r in replies)
    summaries = fake_telnyx.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert "call back" in summaries[0].lower()


def test_discount_request_escalates_to_william(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    resp = text_bot(client, LEAD, "Can you do it for free?")
    assert resp.status_code == 200
    summaries = fake_telnyx.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert "discount_request" in summaries[0]


def test_date_request_never_confirms_booking(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    resp = text_bot(client, LEAD, "Can you shoot Tuesday at 10am?")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("can't lock in" in r for r in replies)
    assert not any("confirmed" in r.lower() and "tuesday" in r.lower() for r in replies)


def test_package_detail_question(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    resp = text_bot(client, LEAD, "What does the Luxury package include?")
    assert resp.status_code == 200
    replies = fake_telnyx.sent_to(LEAD)
    assert any("Luxury" in r and "$549" in r for r in replies)


def test_whats_in_each_package_answers_with_contents(client, fake_telnyx):
    # Regression: "whats in each package" used to loop back to the bare
    # price-list question instead of explaining the packages.
    text_bot(client, LEAD, "Hi")
    text_bot(client, LEAD, "I'm an agent with a listing")
    text_bot(client, LEAD, "130 County Rd")
    text_bot(client, LEAD, "3200 3 bed 3 bath")
    replies = fake_telnyx.sent_to(LEAD)
    # Bare "3200" with no unit is still read as square footage, so the
    # bot recommends Marketing ($389) for 3,200 sq ft.
    assert any("Marketing" in r and "$389" in r for r in replies)

    resp = text_bot(client, LEAD, "whats in each package")
    assert resp.status_code == 200
    answer = fake_telnyx.sent_to(LEAD)[-1]
    assert "25-30 HDR photos" in answer  # Essential contents
    assert "Matterport" in answer  # Signature contents
    assert "drone" in answer.lower()
    # And it resumes the package question instead of dropping the thread.
    assert "leaning toward" in answer


def test_whats_in_each_package_repeat_ask_still_answers(client, fake_telnyx):
    text_bot(client, LEAD, "Hi")
    text_bot(client, LEAD, "I'm an agent with a listing")
    text_bot(client, LEAD, "130 County Rd")
    text_bot(client, LEAD, "3200 3 bed 3 bath")
    text_bot(client, LEAD, "whats in each")
    text_bot(client, LEAD, "whats in each package")
    replies = fake_telnyx.sent_to(LEAD)
    assert any("Matterport" in r for r in replies[-2:])


# --- Signature verification (valid -> 200, tampered -> 401) ---


def _signed_client(tmp_path):
    """App with TELNYX_PUBLIC_KEY set, returning (client, private_key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    settings = Settings(
        telnyx_api_key="test-key",
        telnyx_bot_number=BOT,
        william_cell=WILLIAM,
        telnyx_public_key=public_b64,
        data_dir=tmp_path / "data",
    )
    fake = FakeTelnyxClient()
    app = create_app(settings=settings, telnyx_client=fake)
    return TestClient(app), private, fake


def _sign(private, raw_body: bytes, timestamp: str) -> str:
    return base64.b64encode(
        private.sign(timestamp.encode() + b"|" + raw_body)
    ).decode()


def test_signature_valid_accepted(tmp_path):
    client, private, fake = _signed_client(tmp_path)
    payload = webhook_payload(LEAD, "Hi")
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    resp = client.post(
        "/webhooks/telnyx/sms",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "telnyx-signature-ed25519": _sign(private, raw, ts),
            "telnyx-timestamp": ts,
        },
    )
    assert resp.status_code == 200
    assert len(fake.sent_to(LEAD)) == 1


def test_signature_tampered_rejected_401(tmp_path):
    client, private, fake = _signed_client(tmp_path)
    payload = webhook_payload(LEAD, "Hi")
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    # Sign a DIFFERENT body than the one sent.
    other = json.dumps(webhook_payload(LEAD, "different text")).encode()
    resp = client.post(
        "/webhooks/telnyx/sms",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "telnyx-signature-ed25519": _sign(private, other, ts),
            "telnyx-timestamp": ts,
        },
    )
    assert resp.status_code == 401
    assert fake.sent_to(LEAD) == []


def test_signature_missing_rejected_when_key_set(tmp_path):
    client, private, fake = _signed_client(tmp_path)
    resp = client.post("/webhooks/telnyx/sms", json=webhook_payload(LEAD, "Hi"))
    assert resp.status_code == 401
    assert fake.sent_to(LEAD) == []


def test_signature_stale_timestamp_rejected(tmp_path):
    client, private, fake = _signed_client(tmp_path)
    payload = webhook_payload(LEAD, "Hi")
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()) - 3600)  # 1 hour old
    resp = client.post(
        "/webhooks/telnyx/sms",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "telnyx-signature-ed25519": _sign(private, raw, ts),
            "telnyx-timestamp": ts,
        },
    )
    assert resp.status_code == 401


def test_voice_webhook_acknowledges_empty_payload(client):
    # Voice is no longer a stub: the real voice router is mounted at
    # /webhooks/telnyx/voice and acknowledges malformed payloads with 200.
    resp = client.post("/webhooks/telnyx/voice", json={})
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_duplicate_webhook_retry_ignored(client, fake_telnyx):
    payload = webhook_payload(LEAD, "Hi")
    assert client.post("/webhooks/telnyx/sms", json=payload).status_code == 200
    assert len(fake_telnyx.sent_to(LEAD)) == 1
    # Same event/message ids again (Telnyx retry) — acknowledged, no re-reply.
    resp = client.post("/webhooks/telnyx/sms", json=payload)
    assert resp.status_code == 200
    assert resp.json().get("ignored") == "duplicate"
    assert len(fake_telnyx.sent_to(LEAD)) == 1
