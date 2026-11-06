# Telnyx Research — Listing Pixl SMS + Voice Bot

Researched 2026-11-05 (docs fetched from developers.telnyx.com that day) and probed
live from Legion 2026-11-05. Account: willmac1.wm@gmail.com (trial account).
Bot host: Legion (Windows, always on), FastAPI on port 8102, public base URL
configurable — assumed `https://bot.listingpixl.com` below (swap in the real one).

Base URL for all REST calls: `https://api.telnyx.com/v2`
Auth everywhere: `Authorization: Bearer <TELNYX_API_KEY>` (live key lives in
`C:\ai-comms-service\.env` on Legion as `TELNYX_API_KEY`; the vault-stored key is
dead — 401. Never print, paste, or log the key value.)

---

## 1. Messaging — send SMS

### Send a message
`POST /v2/messages`

```bash
curl -X POST https://api.telnyx.com/v2/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TELNYX_API_KEY" \
  -d '{
    "from": "+16095550100",
    "to": "+16095550101",
    "text": "Hi, this is Listing Pixl! ..."
  }'
```

Required fields: `from` (your Telnyx number, E.164 with `+`), `to` (E.164),
`text`. **`messaging_profile_id` is NOT required in the send body** — Telnyx
resolves the profile from the `from` number's assignment. Optional useful
fields: `media_urls` (MMS), `subject` (MMS), `webhook_url` /
`webhook_failover_url` (per-message delivery-receipt overrides),
`use_profile_webhooks` (bool).

Success response (200):

```json
{
  "data": {
    "record_type": "message",
    "direction": "outbound",
    "id": "b0c7e8cb-6227-4c74-9f32-c7f80c30934b",
    "type": "SMS",
    "messaging_profile_id": "16fd2706-8baf-433b-82eb-8c7fada847da",
    "from": { "phone_number": "+16095550100", "carrier": "Telnyx", "line_type": "Wireless" },
    "to": [{ "phone_number": "+16095550101", "status": "queued", "carrier": "CARRIER", "line_type": "Wireless" }],
    "text": "Hello, world!",
    "encoding": "GSM-7",
    "parts": 1,
    "cost": { "amount": 0.0051, "currency": "USD" }
  }
}
```

`status: "queued"` = accepted. Save `data.id` to track delivery
(`message.sent` / `message.finalized` webhooks). Telnyx auto-splits long
messages and picks GSM-7 vs UCS-2 encoding. SMS from ~$0.004/message
(docs example cost $0.0051).

### Inbound SMS webhook — `message.received`

Telnyx POSTs to the **webhook URL configured on the messaging profile**
assigned to the number. Example payload (shape confirmed from the docs'
schema tables; field order/nesting as documented):

```json
{
  "data": {
    "event_type": "message.received",
    "id": "6a1d5c2e-0000-4000-8000-000000000001",
    "occurred_at": "2026-10-06T18:40:00.000+00:00",
    "record_type": "event",
    "payload": {
      "id": "<message UUID>",
      "record_type": "message",
      "direction": "inbound",
      "type": "SMS",
      "from": { "phone_number": "+16095550101", "carrier": "T-Mobile", "line_type": "Wireless" },
      "to": [{ "phone_number": "+16095550100" }],
      "text": "Hi, I need photos of my house in North Wildwood",
      "encoding": "GSM-7",
      "parts": 1,
      "media": [],
      "messaging_profile_id": "<profile id>",
      "received_at": "2026-10-06T18:40:00.000+00:00"
    }
  },
  "meta": { "attempt": 1, "delivered_to": "https://bot.listingpixl.com/webhooks/telnyx/sms" }
}
```

Key fields for the bot: `data.event_type`, `data.id` (event UUID — idempotency),
`data.payload.from.phone_number`, `data.payload.to[0].phone_number`,
`data.payload.text` (may be `null` for media-only MMS), `data.payload.id`
(message UUID — dedupe key), `data.payload.media[]` for MMS
(`url` — authenticated download, expires in 30 days — `content_type`,
`sha256`, `size`).

Other events on the same webhook URL: `message.sent` (outbound accepted),
`message.finalized` (final delivery status).

**Handler rules (from docs):**
- Respond `2xx` within **2 seconds** (API v2) or Telnyx retries (up to 2
  retries, `meta.attempt` 1–3). Return 200 immediately, process async.
- Deduplicate on `payload.id`.
- Webhook URL hierarchy: per-message `webhook_url` in the send body >
  messaging-profile URL > none (delivery skipped). Set a **failover URL** on
  the profile too.

### Webhook signature verification (Ed25519)

Every webhook carries two headers:
- `telnyx-signature-ed25519` — Base64-encoded Ed25519 signature
- `telnyx-timestamp` — Unix timestamp of signing

Signed message is exactly `"{telnyx-timestamp}|{raw request body}"`
(raw bytes — do NOT parse and re-serialize JSON first). Timestamp must be
within 300 seconds of now. Verify with the account's **public key**:

- Portal: Mission Control → Account Settings → Keys & Credentials → Public Key
- API: `GET /v2/public_key` returns `data.public` (base64) — **but this
  endpoint is 403 on this account today (see §5); copy the key from the
  Portal instead.**
- With the Telnyx Python SDK: `Telnyx(api_key=..., public_key=...)` then
  `client.webhooks.unwrap(raw_body, headers=dict(request.headers))` — raises
  `ValueError` on failure. (Needs `telnyx` + `pynacl`.)
- Same Ed25519 scheme signs AI-assistant webhook tool calls and
  dynamic-variables webhooks.

### Messaging profiles
- List: `GET /v2/messaging_profiles`
- Create: `POST /v2/messaging_profiles` — body needs at minimum `name` and
  `whitelisted_destinations` (required since 2024-03-01; e.g. `["US"]`),
  plus `webhook_url` and `webhook_failover_url`.
- Assign a number to a profile: `PATCH /v2/phone_numbers/{phone_number_id}/messaging`
  with `{"messaging_profile_id": "<id>"}` — or
  `PATCH /v2/messaging_phone_numbers/{e164}` / `POST /v2/messaging_profiles/{id}/phone_numbers`.

### 10DLC (US long-code A2P) — required before texting real customers
A 609 local number is a 10DLC long code. US A2P traffic from it requires a
**brand + campaign registration** through Telnyx (brand ~$4 one-time registry
fee; campaign vetting ~$15; trust score 0–100 sets throughput). Since
2026-02-17 a Business Registration Number (9-digit EIN for a US business) is
mandatory on new submissions. A **sole-proprietor brand** path exists
(Telnyx CLI `telnyx 10dlc brand create --sole-prop ...`; docs:
developers.telnyx.com/docs/messaging/10dlc). **Unregistered long codes are
throttled to ~10 messages/min and filtered/blocked by carriers.** Telnyx-to-
Telnyx test messages work without registration. Toll-free numbers are the
alternative (toll-free verification instead of 10DLC) — not our plan; the bot
number is a 609 local.

---

## 2. Voice AI Agents (Telnyx AI Assistants) — managed, GA

AI Assistants are the managed, configuration-first voice-agent product
(distinct from the beta Agent SDK for building agents in code). An assistant
is created once via API or Portal, then attached to calls by ID.

### Create an assistant
`POST /v2/ai/assistants` — required: `name`, `instructions`
(model is optional; if omitted the API defaults to `zai-org/GLM-5.3-Flash` —
the current Portal-template default. Voice-verified models include
`moonshotai/Kimi-K2.6`, `moonshotai/Kimi-K2.5`, `zai-org/GLM-5.2`.)

```bash
curl -X POST https://api.telnyx.com/v2/ai/assistants \
  -H "Authorization: Bearer $TELNYX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Listing Pixl Receptionist",
    "model": "zai-org/GLM-5.3-Flash",
    "instructions": "You are the phone receptionist for Listing Pixl, a real-estate photography business in Cape May County, NJ. ... The caller is {{full_name}}. Never confirm an action before the tool returns. Ask one clarifying question, then transfer.",
    "greeting": "Thanks for calling Listing Pixl, this call may be recorded. How can I help?",
    "voice_settings": { "voice": "<Telnyx Ultra voice ID, UUID>" },
    "transcription": { "model": "deepgram/flux" },
    "dynamic_variables_webhook_url": "https://bot.listingpixl.com/webhooks/telnyx/dynamic-variables",
    "dynamic_variables_webhook_timeout_ms": 8000,
    "tools": [
      {
        "type": "webhook",
        "name": "log_lead",
        "description": "Save the callers lead details to Listing Pixl",
        "url": "https://bot.listingpixl.com/tools/log-lead",
        "method": "POST",
        "body_parameters": {
          "type": "object",
          "properties": {
            "name": { "type": "string" },
            "phone": { "type": "string" },
            "property_address": { "type": "string" },
            "service": { "type": "string" },
            "notes": { "type": "string" }
          }
        }
      },
      { "type": "built_in", "name": "transfer" }
    ],
    "insight_settings": {
      "insight_schema": {
        "type": "object",
        "properties": {
          "caller_name": { "type": "string" },
          "property_address": { "type": "string" },
          "service_requested": { "type": "string" },
          "callback_number": { "type": "string" }
        }
      }
    },
    "telephony_settings": {
      "greeting_delay_ms": 250,
      "send_message_history_updates": true,
      "conversation_ended_full_history": true
    }
  }'
```

Response: `data.id` is `assistant-<uuid>` — store it; that is what calls
reference. Other useful create fields: `description`, `tags`,
`dynamic_variables` (static map), `enabled_features`, `llm_api_key_ref`
(BYOK LLM), `messaging_settings`, `privacy_settings`, `widget_settings`.
`voice_settings.tool_call_audio` sets a sound played while a tool runs.

Other endpoints:
- `GET /v2/ai/assistants` — list
- `GET /v2/ai/assistants/{assistant_id}` — get (supports
  `?fetch_dynamic_variables_from_webhook=true`, `?call_control_id=...`)
- `POST /v2/ai/assistants/{assistant_id}` — update; saves a **new version and
  makes it live** unless a draft is requested
- `DELETE /v2/ai/assistants/{assistant_id}` — delete (list deleted:
  `GET /v2/ai/assistants/deleted`)
- `POST /v2/ai/assistants/{assistant_id}/chat` — chat with an assistant
  (body: `content`, `conversation_id`)
- `POST /v2/ai/assistants/import` — import from ElevenLabs / Vapi / Retell
  (`provider`, `api_key_ref`)
- Tests: `POST /v2/ai/assistants/tests` (`name`, `destination`,
  `instructions`, `rubric`) and `POST /v2/ai/assistants/tests/{test_id}/runs`
  — scripted-caller tests scored against a rubric; run before promoting a
  new version.

Dynamic variables: `{{variable}}` placeholders in instructions/greeting,
resolved per call from `dynamic_variables`, the
`dynamic_variables_webhook_url` (POST, must reply
`{"dynamic_variables": {...}}`, default timeout 1,500 ms, max 10,000 ms),
SIP headers, or the outbound-call request. System variables for caller/callee
details are provided by Telnyx.

### Tools / function calling — YES, webhook tools work
Tool types: `webhook` (any HTTPS endpoint), `integration` (30 catalog
systems, OAuth once), `mcp_server` (your MCP server), `client_side`,
`built_in` (`transfer`, `handoff`, `sms`, `dtmf`, `refer`). Every assistant
can hang up by default.

Webhook tool call (Telnyx → our server):
- `POST {tool.url}` with the **flat arguments object** as the body
  (e.g. `{"name": "...", "phone": "...", ...}` from `body_parameters`),
  header `x-telnyx-call-control-id` identifying the call, signed Ed25519
  (`telnyx-signature-ed25519` / `telnyx-timestamp` headers).
- Synchronous by default: the assistant waits for the 2xx response (set a
  timeout, recommended 1–5 s) and the returned content becomes the tool
  result. **Fire-and-forget lead logging: set `async: true` on the webhook
  tool** — the assistant keeps talking while the webhook runs.
- For long-running work, use async tools + the **Add Messages API** to inject
  results back into the live conversation later (exact Add Messages path to
  be confirmed against the API reference at build time; conversations are
  addressable via `GET /v2/ai/conversations?metadata->call_control_id=eq.{id}`
  and `GET /v2/ai/conversations/{conversation_id}`).

**End-of-call lead summary:** the cleanest path is NOT a tool call at hangup
— it is the `call.conversation_insights.generated` webhook (§2.3) plus the
`call.conversation.ended` webhook, both of which land on our server with the
full transcript/summary. A webhook tool can also log the lead mid-call
(async) if the assistant gathers the details.

### Routing an inbound call to the assistant
Prerequisite: a **Call Control application** whose `webhook_event_url`
points at our server. Inbound call flow:

1. Telnyx POSTs `call.initiated` to the Call Control webhook URL with
   `data.payload.call_control_id`.
2. Our server answers with the assistant attached:

`POST /v2/calls/{call_control_id}/actions/answer`

```json
{ "assistant": { "id": "assistant-<uuid>" } }
```

Only `assistant.id` is required — the call uses the stored assistant's
configuration. Per-call overrides can be sent alongside the ID
(`assistant.greeting`, `assistant.instructions`, `assistant.tools`,
`assistant.voice_settings.voice`, `assistant.transcription`,
`dynamic_variables` — these merge with stored values; a supplied
`voice_settings`/`transcription` object replaces the whole stored object).
Telnyx warms up the assistant while the call is still ringing, then answers
and emits `call.answered`; the assistant starts automatically — **do not
also send `ai_assistant_start`**. Track pickup via the `call.answered`
webhook (the HTTP 200 can arrive first). `telephony_settings.greeting_delay_ms`
(0–2000, default 250) controls the pause before the greeting.

3. If the assistant can't start (bad ID etc.), Telnyx sends
   `call.conversation.start_failed` instead of `call.conversation.created`
   and the call stays answered — handle it (transfer to William / hang up).

Already-answered call (e.g. after an IVR): attach with
`POST /v2/calls/{call_control_id}/actions/ai_assistant_start`
(body: `assistant` object; `send_message_history_updates: true` optional).

Outbound call with an assistant: `POST /v2/calls` with `connection_id`
(the Call Control application's ID), `to`, `from`, and
`{"assistant": {"id": "..."}}`. TeXML alternative:
`POST /v2/texml/ai_calls/{connection_id}`.

Stop the assistant mid-call:
`POST /v2/calls/{call_control_id}/actions/ai_assistant_stop` — on Call
Control the call stays connected and our app can continue with other
commands (transfer, hangup, ...).

### Transcripts, summaries, insights
- `call.conversation.created` — includes `conversation_id` +
  `call_control_id`. **The `answer` HTTP response does NOT include
  `conversation_id`** — capture it from this webhook and store the pair.
- `call.conversation.ended` — `data.payload.messages[]` has the user and
  assistant text turns by default. Set
  `telephony_settings.conversation_ended_full_history: true` on the
  assistant to get the full history in order, including tool calls and tool
  results (`role: user|assistant|tool|system|developer`). Instructions are
  never included; messages are redacted if data retention is disabled.
- `call.conversation_insights.generated` — fires when the summary/insights
  defined in the assistant's `insight_settings` (JSON schema) are ready.
  This is the structured lead-summary delivery mechanism.
- Live transcript: set `telephony_settings.send_message_history_updates:
  true` (or `send_message_history_updates: true` on `ai_assistant_start`)
  to receive `call.ai_gather.message_history_updated` on every turn, each
  carrying the full history so far.
- Conversation lookup: `GET /v2/ai/conversations` (filter
  `?metadata->call_control_id=eq.{call_control_id}`),
  `GET /v2/ai/conversations/{conversation_id}`.

### Pricing (Telnyx pricing page, Oct–Nov 2026)
- Voice AI engine: **$0.05/min** — orchestration + STT + Telnyx-native TTS
  included. Billed in **60-second increments** (a 15-second call bills a
  full minute).
- LLM tokens billed separately (~$0.006/min for a hosted open-weight model).
- Telephony: US inbound SIP from $0.0032/min + $0.002/min Voice API fee.
- Telnyx estimates a production agent at **~$0.06/min all-in**.
- US local number: **$1.00/month**.
- No platform fee on pay-as-you-go; Growth ($2,000/mo) / Enterprise
  ($5,000/mo) minimums lower telephony/number rates — irrelevant at our
  volume.

---

## 3. Call Control API basics (fallback if Voice AI doesn't fit)

- Create app: `POST /v2/call_control_applications` — required:
  `application_name`, `webhook_event_url`; optional
  `webhook_event_failover_url`, `webhook_api_version` (`"1"` or `"2"` — use
  `"2"`), `webhook_timeout_secs`. Response `data.id` is the `connection_id`
  used in dials.
- List: `GET /v2/call_control_applications`; get/update/delete by ID
  (`PATCH /v2/call_control_applications/{id}`).
- Inbound calls arrive as `call.initiated` webhooks at `webhook_event_url`.
- Answer: `POST /v2/calls/{call_control_id}/actions/answer`
- Play TTS: `POST /v2/calls/{call_control_id}/actions/speak`
  (`{"payload": "Hello...", "voice": "female", "language": "en-US"}`-style;
  exact fields per the Answer/Speak reference at build time)
- Other actions under `/v2/calls/{call_control_id}/actions/*`:
  `hangup`, `reject`, `transfer`, `bridge`, `dial` (via `POST /v2/calls`),
  `record_start` / `record_stop`, `transcription_start` / `transcription_stop`,
  `send_dtmf`, `gather_using_ai`, `ai_assistant_start`, `ai_assistant_stop`,
  `ai_assistant_join`.
- Key voice webhooks: `call.initiated`, `call.answered`, `call.hangup`,
  `call.recording.saved`, `call.recording.transcription.saved`.
- A Call Control application needs an **Outbound Voice Profile** for outbound
  calls; the account already has one Default profile (assigned to the FaxSnap
  Fax Application — per the connector doc, the account allows only one
  Outbound Voice Profile).

---

## 4. Number assignment — one number, voice + SMS

A single Telnyx number carries **two independent assignments** and can do
both at once:
- **Voice:** `connection_id` → the Call Control application (or Fax
  Application, TeXML app, SIP connection). Set via
  `PATCH /v2/phone_numbers/{phone_number_id}` with
  `{"connection_id": "<call-control-app-id>"}`.
- **SMS:** `messaging_profile_id` → the messaging profile. Set via
  `PATCH /v2/phone_numbers/{phone_number_id}/messaging` with
  `{"messaging_profile_id": "<profile-id>"}`.

Confirmed in docs: "SMS and voice keep working on the same number" — assign
the number to a messaging profile for SMS and to a voice connection/
application for voice.

### Buy a number
`POST /v2/number_orders` — body `{"phone_numbers": [{"phone_number": "+1609..."}]}`.
Search first: `GET /v2/available_phone_numbers?filter[country_code]=US&filter[phone_number][starts_with]=609...`
(or Portal: Numbers → Search & Buy, filter area code 609 + SMS + Voice).
US local number ≈ $1.00/month. **Requires account balance — see §5.**

---

## 5. What exists in the account NOW (probed 2026-11-05 from Legion, read-only GETs)

Live key confirmed working: `GET /v2/phone_numbers`, `/v2/messaging_profiles`,
`/v2/call_control_applications`, `/v2/balance` all returned 200.

| Resource | Count | Detail |
|---|---|---|
| Phone numbers | 1 | **+1 302-412-1115**, id `3085075118037141291`, status `active`, type `local`, purchased 2026-11-03. `connection_id` `3085029485217580275` (Fax Application **"FaxSnap"**). `messaging_profile_id: null` — no SMS profile attached. `t38_fax_gateway_enabled: true`, `hd_voice_enabled: true`. Do NOT touch — FaxSnap's number. |
| Messaging profiles | 0 | None exist — create "Listing Pixl" at launch. |
| Call Control applications | 0 | None exist — create "Listing Pixl Voice" at launch. |
| AI assistants | **BLOCKED** | `GET /v2/ai/assistants` → **403, error 10038 "Feature not permitted"** — `detail: "This feature is not permitted at this account level. Refer to https://telnyx.com/upgrade."` |
| Public key (API) | **BLOCKED** | `GET /v2/public_key` → same 403 / 10038. Get the Ed25519 public key from the Portal instead. |
| Balance | — | **balance "-0.01", available_credit "-0.01", pending "0.00", currency USD** — the account is **one cent negative**. Top up before buying the 609 number or the order will fail. |

### The 10038 problem — the account is a trial account
Both AI endpoints return Telnyx error **10038 "Feature not permitted at this
account level"**. The account is still a **trial account** (per prior notes:
"Telnyx account is a trial account — purchase may prompt an upgrade/
verification step"). Voice AI Assistants are gated behind account
verification/upgrade. **SMS + plain Call Control were NOT proven blocked**
(list endpoints returned 200), but nothing has been created yet, so the
first create calls at launch are the real test. Expect the number purchase
itself to prompt an upgrade/verification step, and the AI-assistant create
to stay 403 until the account is upgraded/verified. Plan: top up + verify the
account first; if Voice AI stays blocked, the SMS bot works regardless and
voice can fall back to plain Call Control TTS/gather.

Reuse vs create at launch:
- **Reuse:** nothing — no messaging profile, no Call Control app, no
  assistant exists. The live API key (Legion `.env`) is reused.
- **Create:** messaging profile, Call Control application, AI assistant,
  609 number, 10DLC brand + campaign.

---

## 6. Launch wiring checklist (exact resources + webhook URLs)

Assumed public base: `https://bot.listingpixl.com` (FastAPI :8102 on Legion,
behind the tunnel). Swap if the real hostname differs.

**Pre-flight (account)**
- [ ] Top up balance (currently **-$0.01**) — Portal → Billing.
- [ ] Complete Telnyx account verification/upgrade (trial → full) so
      Voice AI (10038) and number purchase are permitted.
- [ ] Copy the Ed25519 **public key** from Portal → Account Settings →
      Keys & Credentials → Public Key into the server env
      (`TELNYX_PUBLIC_KEY`). API fetch is 403 on this account.
- [ ] Server env: `TELNYX_API_KEY` (from Legion `C:\ai-comms-service\.env`),
      `TELNYX_PUBLIC_KEY`, `TELNYX_BOT_NUMBER` (once bought).

**Buy + attach the number**
- [ ] Buy 609 number with SMS + Voice: `POST /v2/number_orders`
      `{"phone_numbers": [{"phone_number": "+1609..."}]}` (~$1/mo).
- [ ] Create messaging profile "Listing Pixl":
      `POST /v2/messaging_profiles`
      `{"name": "Listing Pixl", "webhook_url": "https://bot.listingpixl.com/webhooks/telnyx/sms", "webhook_failover_url": "https://bot.listingpixl.com/webhooks/telnyx/sms", "whitelisted_destinations": ["US"]}`
      → save `data.id`.
- [ ] Create Call Control application "Listing Pixl Voice":
      `POST /v2/call_control_applications`
      `{"application_name": "Listing Pixl Voice", "webhook_event_url": "https://bot.listingpixl.com/webhooks/telnyx/voice", "webhook_api_version": "2"}`
      → save `data.id` (this is the `connection_id`).
- [ ] Attach the 609 number to both:
      `PATCH /v2/phone_numbers/{id}/messaging` `{"messaging_profile_id": "<profile-id>"}`
      `PATCH /v2/phone_numbers/{id}` `{"connection_id": "<call-control-app-id>"}`
- [ ] 10DLC: register brand (sole-proprietor path; needs EIN) + campaign,
      assign the 609 number to the campaign. Until approved, expect
      ~10 msg/min throttling and carrier filtering on outbound SMS.

**Voice AI assistant (after the 10038 block is cleared)**
- [ ] Create assistant `POST /v2/ai/assistants` (see §2 example) → save
      `data.id` (`assistant-<uuid>`). Fields: `name`, `instructions`,
      `greeting`, `model` (default `zai-org/GLM-5.3-Flash`), `voice_settings`,
      `transcription`, `dynamic_variables_webhook_url`,
      `tools` (webhook `log_lead`, `async: true`; built-in `transfer`),
      `insight_settings` (lead fields), `telephony_settings`
      (`greeting_delay_ms`, `send_message_history_updates`,
      `conversation_ended_full_history`).
- [ ] Test with `POST /v2/ai/assistants/tests` before going live.

**Server endpoints to build (FastAPI :8102)**
- [ ] `POST /webhooks/telnyx/sms` — verify Ed25519 signature, handle
      `message.received` → bot logic → reply via `POST /v2/messages`
      (`from` = 609 number, `to` = sender). Also accept `message.sent` /
      `message.finalized` (log only). Respond 200 in <2 s, dedupe on
      `payload.id`.
- [ ] `POST /webhooks/telnyx/voice` — verify signature; `call.initiated` →
      `POST /v2/calls/{call_control_id}/actions/answer`
      `{"assistant": {"id": "<assistant-id>"}}`; `call.conversation.created`
      → store `conversation_id` ↔ `call_control_id`; `call.conversation.ended`
      → store transcript; `call.conversation_insights.generated` → structured
      lead → notify William; `call.conversation.start_failed` → transfer/
      hangup fallback.
- [ ] `POST /tools/log-lead` — assistant webhook tool; verify signature,
      accept flat args `{name, phone, property_address, service, notes}`,
      store lead, return 2xx quickly (tool is `async`, so the reply body is
      not spoken back).
- [ ] `POST /webhooks/telnyx/dynamic-variables` (optional) — return
      `{"dynamic_variables": {...}}` for `{{...}}` placeholders; keep under
      1.5 s (or raise `dynamic_variables_webhook_timeout_ms` to 8000).

**Smoke test at launch**
- [ ] Text the 609 number from a personal phone → auto-reply arrives.
- [ ] Call the 609 number → assistant greets, gathers name/address/service,
      logs lead, transcript + insights webhook received.
- [ ] Confirm FaxSnap still works: +1 302-412-1115 untouched, still on the
      FaxSnap Fax Application.

## Sources
- developers.telnyx.com — Messaging: send-message, receive-message,
  receiving-webhooks; Voice: conversational-ai/quickstart,
  programmable-voice/ai-assistant-start; Inference: ai-assistants
  (async-tools, create-assistant), 10DLC docs; API reference via
  team-telnyx/ai skills (ai-assistants-curl) and telnyx.com pricing pages.
- Live probe: PowerShell on Legion (`C:\telnyx-probe.ps1`, results in
  `C:\telnyx-probe\results.json`), 2026-11-05, GETs only, $0 spent.

## Open items / to confirm at build time
- Exact Add Messages API path (for injecting async tool results mid-call).
- Exact `speak` action body fields (fallback TTS path).
- Whether plain Call Control app creation + inbound calls work on the trial
  account before upgrade (list calls work; creates untested — GETs only in
  this probe).
- 10DLC sole-proprietor brand: confirm EIN requirement for William (EIN
  mandatory on new submissions since 2026-02-17).
