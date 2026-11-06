"""Tests for the Listing Pixl voice bot.

All tests run with $0 and no network: Telnyx HTTP calls and SMS sends
are faked (see conftest.py) and the webhooks are exercised through
TestClient.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice.config import VoiceSettings
from voice.webhooks import create_voice_app

VOICE_DIR = Path(__file__).resolve().parents[1]

BOT = "+16095550127"
WILLIAM = "+16095550199"  # fake William number for tests
CALLER = "+16095551111"
CCID = "call-control-123"


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


# --- voice webhook: call.initiated ------------------------------------------


def test_call_initiated_answers_with_assistant(client, fake_http):
    resp = client.post("/webhooks/telnyx/voice", json=initiated_payload())
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    answers = fake_http.actions("answer")
    assert len(answers) == 1
    assert answers[0]["json"] == {"assistant": {"id": "assistant-test-uuid"}}
    assert CCID in answers[0]["url"]


def test_call_initiated_ivr_fallback_when_flag_off(tmp_path, fake_http, fake_sms):
    settings = VoiceSettings(
        telnyx_api_key="test-key",
        telnyx_bot_number=BOT,
        william_cell=WILLIAM,
        telnyx_assistant_id="assistant-test-uuid",
        use_voice_ai=False,
        data_dir=tmp_path / "data",
    )
    app = create_voice_app(
        settings=settings, telnyx_http=fake_http, sms_client=fake_sms
    )
    with TestClient(app) as test_client:
        resp = test_client.post("/webhooks/telnyx/voice", json=initiated_payload())
    assert resp.status_code == 200
    answers = fake_http.actions("answer")
    assert len(answers) == 1
    # Plain answer: no assistant attached.
    assert "assistant" not in answers[0]["json"]
    speaks = fake_http.actions("speak")
    assert len(speaks) == 1
    assert "Listing Pixl" in speaks[0]["json"]["payload"]
    assert "$200" in speaks[0]["json"]["payload"] or "200 dollars" in speaks[0]["json"]["payload"]
    gathers = fake_http.actions("gather_using_ai")
    assert len(gathers) == 1
    params = gathers[0]["json"]["parameters"]
    assert params["type"] == "object"
    assert "name" in params["properties"]
    assert "property_address" in params["properties"]


def test_call_initiated_ivr_fallback_when_no_assistant_id(tmp_path, fake_http, fake_sms):
    settings = VoiceSettings(
        telnyx_api_key="test-key",
        telnyx_bot_number=BOT,
        william_cell=WILLIAM,
        telnyx_assistant_id="",  # not created yet
        use_voice_ai=True,
        data_dir=tmp_path / "data",
    )
    app = create_voice_app(
        settings=settings, telnyx_http=fake_http, sms_client=fake_sms
    )
    with TestClient(app) as test_client:
        resp = test_client.post("/webhooks/telnyx/voice", json=initiated_payload())
    assert resp.status_code == 200
    # Falls back to IVR instead of leaving the caller unanswered.
    assert len(fake_http.actions("answer")) == 1
    assert len(fake_http.actions("gather_using_ai")) == 1


def test_voice_webhook_other_events_acknowledged(client, fake_http):
    for event in ("call.answered", "call.hangup", "call.recording.saved"):
        resp = client.post(
            "/webhooks/telnyx/voice",
            json=voice_payload(event, {"call_control_id": CCID}),
        )
        assert resp.status_code == 200
    # Unknown event: still 200, no crash, no Call Control spam.
    resp = client.post(
        "/webhooks/telnyx/voice",
        json=voice_payload("call.something.new", {"call_control_id": CCID}),
    )
    assert resp.status_code == 200
    assert fake_http.calls == []


def test_voice_webhook_malformed_payloads_no_crash_200(client, fake_http):
    assert client.post("/webhooks/telnyx/voice", json={}).status_code == 200
    assert client.post(
        "/webhooks/telnyx/voice", json={"data": {"event_type": "call.initiated"}}
    ).status_code == 200
    resp = client.post(
        "/webhooks/telnyx/voice",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert fake_http.calls == []


def test_conversation_created_stores_ids(client, store):
    resp = client.post(
        "/webhooks/telnyx/voice",
        json=voice_payload(
            "call.conversation.created",
            {"call_control_id": CCID, "conversation_id": "conv-1"},
        ),
    )
    assert resp.status_code == 200
    call = store.get_call(CCID)
    assert call["conversation_id"] == "conv-1"


def test_start_failed_falls_back_to_speak_and_hangup(client, fake_http):
    resp = client.post(
        "/webhooks/telnyx/voice",
        json=voice_payload("call.conversation.start_failed", {"call_control_id": CCID}),
    )
    assert resp.status_code == 200
    assert len(fake_http.actions("speak")) == 1
    assert len(fake_http.actions("hangup")) == 1


# --- IVR gather result -------------------------------------------------------


def test_ivr_gather_result_stores_lead_texts_william_and_hangs_up(
    client, fake_http, fake_sms, store
):
    client.post("/webhooks/telnyx/voice", json=initiated_payload())
    fake_http.calls.clear()
    resp = client.post(
        "/webhooks/telnyx/voice",
        json=voice_payload(
            "call.ai_gather.ended",
            {
                "call_control_id": CCID,
                "result": {
                    "name": "Sarah",
                    "property_address": "123 Beach Ave, Cape May",
                    "shoot_type": "selling",
                    "package_interest": "Standard",
                    "ready_when": "next week",
                },
            },
        ),
    )
    assert resp.status_code == 200
    summaries = fake_sms.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert "Sarah" in summaries[0]
    assert "123 Beach Ave, Cape May" in summaries[0]
    assert "Standard" in summaries[0]
    assert len(fake_http.actions("hangup")) == 1
    leads = store.all_leads()
    assert len(leads) == 1
    assert leads[0]["source"] == "voice-ivr"
    assert leads[0]["phone"] == CALLER  # filled from the call record


def test_ivr_gather_empty_result_texts_caller_number_only_and_hangs_up(
    client, fake_http, fake_sms
):
    # Gather got no spoken details, but we still have the caller ID —
    # that alone is worth one short missed-call note to William.
    client.post("/webhooks/telnyx/voice", json=initiated_payload())
    resp = client.post(
        "/webhooks/telnyx/voice",
        json=voice_payload(
            "call.ai_gather.ended", {"call_control_id": CCID, "result": {}}
        ),
    )
    assert resp.status_code == 200
    summaries = fake_sms.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert CALLER in summaries[0]
    assert "Sarah" not in summaries[0]
    assert len(fake_http.actions("hangup")) == 1


# --- log-lead tool -----------------------------------------------------------


def test_log_lead_stores_and_texts_william(client, fake_sms, store):
    resp = client.post("/tools/log-lead", json=LEAD_ARGS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["summary_sent"] is True
    summaries = fake_sms.sent_to(WILLIAM)
    assert len(summaries) == 1
    summary = summaries[0]
    assert "Sarah" in summary
    assert CALLER in summary
    assert "123 Beach Ave, Cape May" in summary
    assert "selling" in summary
    assert "Standard" in summary
    assert "next week" in summary
    leads = store.all_leads()
    assert len(leads) == 1
    assert leads[0]["source"] == "voice-assistant"


def test_log_lead_empty_payload_rejected(client, fake_sms, store):
    resp = client.post("/tools/log-lead", json={})
    assert resp.status_code == 400
    assert fake_sms.sent == []
    assert store.all_leads() == []


def test_log_lead_blacklisted_caller_flagged(client, fake_sms, store):
    args = dict(LEAD_ARGS, name="Jen Maguire", message="Need photos please")
    resp = client.post("/tools/log-lead", json=args)
    assert resp.status_code == 200
    summaries = fake_sms.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert "BLACKLISTED" in summaries[0]
    leads = store.all_leads()
    assert "blacklisted" in leads[0]["flags"]


def test_log_lead_uses_caller_number_when_phone_missing(client, fake_sms, store):
    client.post("/webhooks/telnyx/voice", json=initiated_payload())
    args = {k: v for k, v in LEAD_ARGS.items() if k != "phone"}
    resp = client.post(
        "/tools/log-lead",
        json=args,
        headers={"x-telnyx-call-control-id": CCID},
    )
    assert resp.status_code == 200
    leads = store.all_leads()
    assert leads[0]["phone"] == CALLER


# --- insights / conversation ended ------------------------------------------


def test_insights_generated_structured_lead_texts_william(client, fake_sms, store):
    resp = client.post(
        "/webhooks/telnyx/insights",
        json=voice_payload(
            "call.conversation_insights.generated",
            {
                "call_control_id": CCID,
                "conversation_id": "conv-1",
                "insights": dict(LEAD_ARGS),
            },
        ),
    )
    assert resp.status_code == 200
    summaries = fake_sms.sent_to(WILLIAM)
    assert len(summaries) == 1
    assert "Sarah" in summaries[0]
    leads = store.all_leads()
    assert len(leads) == 1
    assert leads[0]["source"] == "voice-insights"


def test_conversation_ended_stores_transcript_and_texts_once(client, fake_sms, store):
    client.post("/webhooks/telnyx/voice", json=initiated_payload())
    messages = [
        {"role": "assistant", "content": "Hi, thanks for calling Listing Pixl."},
        {"role": "user", "content": "Hi, I'm Sarah, selling my house in Cape May."},
    ]
    resp = client.post(
        "/webhooks/telnyx/voice",
        json=voice_payload(
            "call.conversation.ended",
            {"call_control_id": CCID, "messages": messages},
        ),
    )
    assert resp.status_code == 200
    call = store.get_call(CCID)
    assert "Sarah" in (call["transcript"] or "")
    assert len(call["transcript_messages"]) == 2
    assert len(fake_sms.sent_to(WILLIAM)) == 1

    # A later log-lead for the same call must NOT text William a second time.
    resp = client.post(
        "/tools/log-lead",
        json=LEAD_ARGS,
        headers={"x-telnyx-call-control-id": CCID},
    )
    assert resp.status_code == 200
    assert resp.json()["summary_sent"] is False
    assert len(fake_sms.sent_to(WILLIAM)) == 1


def test_insights_after_log_lead_sends_no_second_sms(client, fake_sms):
    client.post(
        "/tools/log-lead",
        json=LEAD_ARGS,
        headers={"x-telnyx-call-control-id": CCID},
    )
    assert len(fake_sms.sent_to(WILLIAM)) == 1
    resp = client.post(
        "/webhooks/telnyx/insights",
        json=voice_payload(
            "call.conversation_insights.generated",
            {"call_control_id": CCID, "insights": dict(LEAD_ARGS)},
        ),
    )
    assert resp.status_code == 200
    assert len(fake_sms.sent_to(WILLIAM)) == 1  # still one


def test_insights_malformed_no_crash(client, fake_sms):
    assert client.post("/webhooks/telnyx/insights", json={}).status_code == 200
    assert client.post(
        "/webhooks/telnyx/insights",
        json=voice_payload("call.conversation.ended", {"call_control_id": CCID}),
    ).status_code == 200
    assert fake_sms.sent == []


# --- dynamic variables -------------------------------------------------------


def test_dynamic_variables(client):
    resp = client.post("/voice/dynamic-variables", json={})
    assert resp.status_code == 200
    dv = resp.json()["dynamic_variables"]
    assert dv["business_name"] == "Listing Pixl"
    assert dv["website"] == "listingpixl.com"
    assert dv["email"] == "ListingPixl.Media@gmail.com"
    assert dv["service_area"] == "Cape May County, NJ"
    assert "$200" in dv["packages"]
    assert "$815" in dv["packages"]
    assert "$800" in dv["vacation_rental_shoot"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["assistant_configured"] is True
    assert body["voice_ai_enabled"] is True


# --- assistant config --------------------------------------------------------


def test_assistant_config_contents():
    config = json.loads((VOICE_DIR / "assistant_config.json").read_text())
    assistant = config["assistant"]
    assert assistant["name"] == "Listing Pixl"
    assert assistant["model"] == "zai-org/GLM-5.3-Flash"
    assert "Listing Pixl" in assistant["greeting"]

    instructions = assistant["instructions"]
    # Exact current pricing, every tier + add-ons + minimum + vacation.
    for snippet in (
        "Essential $200", "Standard $290", "Marketing $389",
        "Luxury $549", "Signature $815",
        "drone $145", "video $265", "Matterport $185",
        "floor plan $90", "twilight $35", "staging $35",
        "marketing kit $39", "Minimum $250", "$800 per property",
    ):
        assert snippet in instructions, snippet
    # Retired prices must never appear. (The phrase "loyalty rate" appears
    # once in the instructions as a prohibition — "Never mention a loyalty
    # rate" — which is correct; the banned string is the rate itself.)
    for banned in ("$295", "$475", "$750", "Showcase", "Premiere", "$250 loyalty"):
        assert banned not in instructions, banned
    # Take-info-not-book rule + blacklist + callback offer.
    assert "Never promise a date or time" in instructions
    assert "never say a shoot is confirmed or booked" in instructions
    assert "Jen Maguire" in instructions
    assert "Jennifer M. Dwyer" in instructions
    assert "James Sofroney" in instructions
    assert "Blue Homes Group" in instructions
    # The assistant is not Will.
    assert "NOT Will or William" in instructions

    # Voice: synthetic Telnyx Ultra pick (Clara), NOT a clone of William.
    voice = assistant["voice_settings"]["voice"]
    assert voice == "Telnyx.Ultra.Clara"
    assert "clone" not in voice.lower()
    assert "William" not in voice

    # Tools: async log_lead webhook + built-in transfer.
    tools = assistant["tools"]
    log_lead = next(t for t in tools if t["name"] == "log_lead")
    assert log_lead["type"] == "webhook"
    assert log_lead["async"] is True
    assert log_lead["url"] == "https://bot.listingpixl.com/tools/log-lead"
    assert log_lead["method"] == "POST"
    assert next(t for t in tools if t["name"] == "transfer")["type"] == "built_in"

    # Insight schema: exactly the lead fields the task specifies.
    schema = assistant["insight_settings"]["insight_schema"]
    assert set(schema["properties"]) == {
        "name", "phone", "address", "shoot_type", "package_interest",
        "ready_when", "message", "needs_callback",
    }

    # Telephony settings.
    telephony = assistant["telephony_settings"]
    assert telephony["conversation_ended_full_history"] is True
    assert telephony["send_message_history_updates"] is True
    assert telephony["greeting_delay_ms"] == 250

    # No secrets anywhere in the config file (no key material, no Bearer token).
    raw = (VOICE_DIR / "assistant_config.json").read_text()
    assert "TELNYX_API_KEY" not in raw
    assert "Bearer " not in raw
    assert "sk-" not in raw


# --- signature verification --------------------------------------------------


def _signed_client(tmp_path, fake_http, fake_sms):
    """App with TELNYX_PUBLIC_KEY set; returns (client, private_key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    settings = VoiceSettings(
        telnyx_api_key="test-key",
        telnyx_bot_number=BOT,
        william_cell=WILLIAM,
        telnyx_assistant_id="assistant-test-uuid",
        telnyx_public_key=public_b64,
        data_dir=tmp_path / "data",
    )
    app = create_voice_app(
        settings=settings, telnyx_http=fake_http, sms_client=fake_sms
    )
    return TestClient(app), private, fake_http, fake_sms


def _sign(private, raw_body: bytes, timestamp: str) -> str:
    return base64.b64encode(
        private.sign(timestamp.encode() + b"|" + raw_body)
    ).decode()


def test_signature_valid_accepted(tmp_path, fake_http, fake_sms):
    client, private, fake_http, _fake_sms = _signed_client(tmp_path, fake_http, fake_sms)
    raw = json.dumps(initiated_payload()).encode()
    ts = str(int(time.time()))
    resp = client.post(
        "/webhooks/telnyx/voice",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "telnyx-signature-ed25519": _sign(private, raw, ts),
            "telnyx-timestamp": ts,
        },
    )
    assert resp.status_code == 200
    assert len(fake_http.actions("answer")) == 1


def test_signature_tampered_rejected_401(tmp_path, fake_http, fake_sms):
    client, private, fake_http, _fake_sms = _signed_client(tmp_path, fake_http, fake_sms)
    raw = json.dumps(initiated_payload()).encode()
    other = json.dumps(voice_payload("call.hangup", {"call_control_id": CCID})).encode()
    ts = str(int(time.time()))
    resp = client.post(
        "/webhooks/telnyx/voice",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "telnyx-signature-ed25519": _sign(private, other, ts),
            "telnyx-timestamp": ts,
        },
    )
    assert resp.status_code == 401
    assert fake_http.calls == []


def test_signature_missing_rejected_when_key_set(tmp_path, fake_http, fake_sms):
    client, _private, fake_http, _fake_sms = _signed_client(tmp_path, fake_http, fake_sms)
    resp = client.post("/webhooks/telnyx/voice", json=initiated_payload())
    assert resp.status_code == 401
    assert fake_http.calls == []


def test_signature_stale_timestamp_rejected(tmp_path, fake_http, fake_sms):
    client, private, fake_http, _fake_sms = _signed_client(tmp_path, fake_http, fake_sms)
    raw = json.dumps(initiated_payload()).encode()
    ts = str(int(time.time()) - 3600)  # 1 hour old
    resp = client.post(
        "/webhooks/telnyx/voice",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "telnyx-signature-ed25519": _sign(private, raw, ts),
            "telnyx-timestamp": ts,
        },
    )
    assert resp.status_code == 401


# --- create_assistant.py (dry-run only — never hits Telnyx) ------------------


def _run_create_script(*args: str, env: dict | None = None):
    cmd = [
        sys.executable,
        str(VOICE_DIR / "create_assistant.py"),
        *args,
    ]
    run_env = dict(env) if env is not None else {}
    # Never pass a real key; dry-run must not need one.
    run_env.pop("TELNYX_API_KEY", None)
    return subprocess.run(cmd, capture_output=True, text=True, env=run_env, timeout=60)


def test_create_assistant_dry_run_prints_body_sends_nothing():
    result = _run_create_script("--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout.split("\n--- DRY RUN")[0])
    assert body["name"] == "Listing Pixl"
    assert body["voice_settings"]["voice"] == "Telnyx.Ultra.Clara"
    assert any(t["name"] == "log_lead" for t in body["tools"])
    assert "DRY RUN" in result.stdout
    # The unresolved-voice warning goes to stderr (short name, not a UUID).
    assert "Telnyx.Ultra.<uuid>" in result.stderr


def test_create_assistant_dry_run_voice_id_override():
    uuid = "12345678-1234-1234-1234-1234567890ab"
    result = _run_create_script("--dry-run", "--voice-id", f"Telnyx.Ultra.{uuid}")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout.split("\n--- DRY RUN")[0])
    assert body["voice_settings"]["voice"] == f"Telnyx.Ultra.{uuid}"
    assert "WARNING" not in result.stderr


def test_create_assistant_dry_run_base_url_override():
    result = _run_create_script("--dry-run", "--base-url", "https://voice.example.com")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout.split("\n--- DRY RUN")[0])
    assert body["dynamic_variables_webhook_url"] == (
        "https://voice.example.com/voice/dynamic-variables"
    )
    log_lead = next(t for t in body["tools"] if t["name"] == "log_lead")
    assert log_lead["url"] == "https://voice.example.com/tools/log-lead"


def test_create_assistant_real_run_refuses_without_key():
    # No key in env and no Legion .env on this Linux VM: exit 2, no network.
    result = _run_create_script(
        "--voice-id", "Telnyx.Ultra.12345678-1234-1234-1234-1234567890ab"
    )
    assert result.returncode == 2
    assert "no Telnyx API key" in result.stderr
