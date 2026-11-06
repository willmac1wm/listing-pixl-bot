# Listing Pixl Voice Bot

Inbound phone bot for Listing Pixl (real-estate photography, Cape May
County NJ). It answers calls on the 609 Telnyx number, quotes packages,
captures lead details, and texts a concise lead summary to William.
It takes information — it never books, never promises a date/time,
and never takes a payment or deposit.

## Voice: synthetic Telnyx voice, NOT a clone of William

The assistant speaks with **Telnyx Ultra "Clara"** — a standard
synthetic Telnyx text-to-speech voice (female, en-US, clear standard
US accent, listed by Telnyx as a general-purpose IVR/AI voice). It is
**not a clone of William's voice**: no voice clone is created or used
anywhere in this package, no recording of William is needed, and the
assistant instructions say it is a synthetic assistant, never Will.
The IVR fallback likewise uses Telnyx synthetic TTS (`speak` action,
voice "female").

One launch detail: Telnyx Ultra voice IDs for AI Assistants are UUIDs
(`Telnyx.Ultra.<uuid>`), so the short name `Telnyx.Ultra.Clara` in
`assistant_config.json` is the *named pick*, not a verified working
ID. At launch, copy Clara's exact UUID from the Portal voice library
(or `GET /v2/text-to-speech/voices`) and either edit
`voice_settings.voice` or pass `--voice-id Telnyx.Ultra.<uuid>`
(or `TELNYX_VOICE_ID`) to `create_assistant.py`. The script warns and
refuses a real create with an unresolved voice unless
`--allow-unresolved-voice` is passed; `--dry-run` only warns.

## How the pieces fit together

```
Inbound call (609 number, Call Control app)
  -> POST /webhooks/telnyx/voice  (call.initiated)
       USE_VOICE_AI=true  + TELNYX_ASSISTANT_ID set
         -> POST /v2/calls/{id}/actions/answer {"assistant": {"id": ...}}
            Telnyx AI Assistant "Listing Pixl" talks to the caller
         -> assistant calls POST /tools/log-lead (async log_lead tool)
            with the gathered details
         -> Telnyx sends call.conversation.created / .ended /
            .insights.generated to the same webhook (and/or
            /webhooks/telnyx/insights) with transcript + structured lead
       USE_VOICE_AI=false, or no assistant id, or assistant start fails
         -> IVR fallback: answer, speak greeting + pricing menu,
            gather_using_ai (name / address / shoot type / package /
            ready-when / message), then store + text William + hang up
  -> every path ends the same way: lead stored in data/voice_calls.json
     and ONE concise SMS to William at +16097807027 (deduped per call —
     whichever of log-lead / insights / conversation-ended sends first
     wins; later ones only store)
  -> POST /voice/dynamic-variables returns business details the
     assistant can use as {{business_name}} etc.
```

## Files

| File | What it is |
|---|---|
| `assistant_config.json` | Assistant definition ready to POST to `POST /v2/ai/assistants`. `assistant` is the exact request body; `_launch_notes` (never sent) records the trial-account block, the voice-UUID step, and the transfer-destination step. |
| `create_assistant.py` | Launch-time script (run **on Legion only**). Reads the config, POSTs it, prints the assistant id. `--dry-run` prints the body and sends nothing. Reads the key from `TELNYX_API_KEY` or Legion `C:\ai-comms-service\.env`; never prints/logs the key. |
| `webhooks.py` | FastAPI router + `create_voice_app()` factory. Endpoints: `POST /webhooks/telnyx/voice`, `POST /tools/log-lead`, `POST /voice/dynamic-variables`, `POST /webhooks/telnyx/insights`, `GET /health`. Importable later into the main app: `from voice.webhooks import router`. |
| `ivr_fallback.py` | IVR fallback flow (answer / speak / gather_using_ai / hang up) + gather-result handling. |
| `call_control.py` | Thin Telnyx Call Control client (answer, answer-with-assistant, speak, gather_using_ai, hangup). |
| `sms_sender.py` | Standalone Telnyx SMS sender (`TelnyxSmsClient`, `send_sms()`). Does not depend on the SMS agent's files. |
| `config.py` | Settings from env vars only. |
| `store.py` | JSON store for calls + leads (`data/voice_calls.json`, separate from the SMS bot's `leads.json`). |
| `leads.py` | Lead normalisation, blacklist check, lead-summary text. |
| `signatures.py` | Ed25519 webhook signature verification. |
| `tests/` | pytest suite (TestClient + fakes, $0, no network). |
| `.env.example` | Env vars with placeholders only. |

## Assistant behaviour (what the instructions enforce)

- Warm, short, plain. No hype, no discounts.
- Exact pricing only: Essential $200, Standard $290, Marketing $389,
  Luxury $549, Signature $815; add-ons drone $145, video $265,
  Matterport $185, floor plan $90, twilight $35/photo, staging
  $35/photo, marketing kit $39; minimum $250/shoot ($200 each for
  same-location paired shoots); vacation-rental/Airbnb $800/property.
  Retired prices (Essential $295 / Showcase $475 / Premiere $750) are
  never quoted, and the $250 Jason Ruffenach loyalty rate is never
  mentioned.
- Asks one or two questions at a time: name; selling vs Airbnb/vacation
  rental; property address; beds/baths/sq ft if known; when ready;
  package interest.
- Never promises a date/time or confirms a booking; never takes
  payment/deposit. Discounts, custom prices, date promises, payment,
  outside Cape May County, models/staging/extra crew, angry/confused/
  scam-sounding callers: offer a callback, take a message, flag it in
  the summary for William.
- Do-not-engage: Jen Maguire / Jennifer M. Dwyer, anyone at Blue Homes
  Group, James W. Sofroney Jr. — the assistant politely ends the call
  and flags it in the log. (The server also flags these names in any
  lead summary it texts to William.)

## Environment variables

| Var | Required | Notes |
|---|---|---|
| `TELNYX_API_KEY` | yes | Live key (Legion `C:\ai-comms-service\.env`). |
| `TELNYX_BOT_NUMBER` | yes | 609 number, E.164 (bought at launch). |
| `WILLIAM_CELL` | no | Default `+16097807027`. |
| `TELNYX_ASSISTANT_ID` | at launch | `assistant-<uuid>` from `create_assistant.py`. |
| `TELNYX_VOICE_ID` | no | Override voice, `Telnyx.Ultra.<uuid>`. |
| `TELNYX_PUBLIC_KEY` | prod | Base64 Ed25519 public key from the Portal. Optional for local testing; without it unsigned webhooks are accepted (warning logged). |
| `USE_VOICE_AI` | no | `true` (default) = AI assistant; `false` = IVR fallback. |
| `PUBLIC_BASE_URL` | no | Default `https://bot.listingpixl.com`. |
| `DATA_DIR` | no | Default `./data`. |
| `LOG_LEVEL` | no | Default `INFO`. |

## Launch sequence

Pre-flight (account) — see `TELNYX_RESEARCH.md` §5–§6: top up the
balance (was -$0.01), complete Telnyx account verification/upgrade,
copy the Ed25519 public key from the Portal into `TELNYX_PUBLIC_KEY`,
buy the 609 number, create the messaging profile + Call Control app
"Listing Pixl Voice" (webhook `https://bot.listingpixl.com/webhooks/telnyx/voice`),
attach the number to both, and register 10DLC.

Then, on Legion:

```powershell
# 1. Check what would be sent (safe to run now — sends nothing):
python C:\path\to\voice\create_assistant.py --dry-run

# 2. Resolve Clara's voice UUID (Portal voice library, or
#    GET /v2/text-to-speech/voices) and create the assistant:
python C:\path\to\voice\create_assistant.py --voice-id Telnyx.Ultra.<uuid>
# -> prints "Assistant created: assistant-<uuid>"

# 3. Put that id in the server env as TELNYX_ASSISTANT_ID and set the
#    transfer tool's destination to William's cell in the Portal
#    (the built-in transfer tool has no destination configured yet).

# 4. Start the server (from the project root; the voice package is
#    importable standalone, or mount `voice.webhooks.router` in the
#    main app):
uvicorn voice.webhooks:app --host 0.0.0.0 --port 8102

# 5. Smoke test: call the 609 number -> assistant greets, gathers
#    name/address/service, logs the lead, and William gets one text.
#    Confirm FaxSnap (+1 302-412-1115) still works — untouched.
```

If Voice AI is still unavailable at launch, set `USE_VOICE_AI=false`
and the IVR fallback answers instead — no assistant needed.

## Tests

```bash
# from ~/workspace/listing-pixl-bot
.venv/bin/python -m pytest voice/tests -q
# 28 tests, $0, no network (Telnyx HTTP + SMS are faked)
```

Covered: `call.initiated` answers with the assistant id; IVR fallback
when the flag is off or no assistant id exists; log-lead stores a lead
and texts William (incl. blacklist flagging and caller-number fill-in);
insights + conversation-ended store transcripts and text William at
most once per call; dynamic variables; signature valid/tampered/
missing/stale; malformed payloads never crash the webhook; the
assistant config contents (exact pricing, no retired prices, no
loyalty rate, blacklist, take-info-not-book rule, async log_lead,
insight schema, telephony settings, synthetic Clara voice); and
`create_assistant.py --dry-run` (+ voice-id / base-url overrides, and
refusal to run for real without a key).

## What could NOT be tested (blocked until launch)

- **Assistant creation** — `POST /v2/ai/assistants` returns 403 error
  10038 on this trial account. Only `--dry-run` was exercised; the
  real create must wait for account verification/upgrade. The script
  explains the 403 in plain language if it still hits it.
- **A live call** — no 609 number bought yet, no Call Control app, no
  assistant id. Answer-with-assistant, greeting, turn-taking, and the
  `call.conversation.start_failed` path are unproven end-to-end.
- **Transcript / insights delivery** — `call.conversation.ended` and
  `call.conversation_insights.generated` shapes are handled from the
  documented payload shapes; the real payloads may carry extra fields
  or different nesting (the handlers tolerate unknown/missing fields
  and log unhandled events, but the exact live shape is unverified).
- **Voice UUID for Clara** — the short name `Telnyx.Ultra.Clara` in
  the config is the named pick; its UUID must be resolved at launch
  (see above). If the API rejects the short name, the create fails
  cleanly with the warning — nothing is half-created.
- **Transfer tool destination** — the built-in `transfer` tool is in
  the config but has no destination; set it in the Portal before
  going live (until then the assistant offers a callback instead).
- **IVR `gather_using_ai` result event** — the request shape
  (`parameters` JSON Schema) is from the Telnyx API reference, but
  the result webhook's exact event name/shape is best-effort: the
  handler accepts both `call.ai_gather.ended` and `call.gather.ended`
  and several result nestings. Confirm against the first live IVR call.
- **Ed25519 verification against real Telnyx signatures** — verified
  in tests with generated keys; the Portal public key must be copied
  into `TELNYX_PUBLIC_KEY` (the `GET /v2/public_key` API is also 403
  on this account).
- **10DLC / SMS deliverability** — the summary SMS path is the same
  `POST /v2/messages` call the SMS bot uses; carrier filtering on an
  unregistered 609 long code (~10 msg/min, possible filtering) applies
  until the brand + campaign are approved.

## Costs at launch

- Voice AI: ~$0.06/min all-in (Telnyx estimate). 60-second billing
  increments.
- 609 number: ~$1.00/month. SMS summaries: ~$0.005 each.
- Nothing was spent building this: $0, no Telnyx calls made.
