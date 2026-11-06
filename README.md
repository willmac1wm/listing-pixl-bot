# Listing Pixl Bot (SMS + Voice)

Inbound SMS auto-reply + voice-call answering and lead capture for
**Listing Pixl** (real-estate photography, Cape May County NJ —
listingpixl.com) via **Telnyx**.

The bot only replies to inbound texts and calls. It takes a lead's name,
property address, selling vs Airbnb/vacation rental, beds/baths/size,
readiness, and package interest — one or two questions per message —
then texts a concise lead summary to William. It never books, never
promises a date/time, never sends marketing, and never offers discounts.

## What it does

- `POST /webhooks/telnyx/sms` — Telnyx `message.received` webhook.
  Verifies the Ed25519 signature when `TELNYX_PUBLIC_KEY` is set
  (signed message is `{timestamp}|{raw_body}`, 5-minute tolerance).
  Unsigned webhooks are accepted only when no key is configured
  (local testing) and a warning is logged.
- Conversation state machine per phone number:
  `new → asked-type → asked-address → asked-details → asked-package → lead-complete`
- Intents: greeting, pricing, package details, what's included, service
  area, availability (info taken, never confirmed), talk-to-Will,
  opt-out/STOP, thanks/goodbye.
- Escalations flagged to William (in the lead summary, not decided by
  the bot): discounts / free shoots, date-time promises, payment /
  deposit / cancellation, outside Cape May County, models / staging /
  extra crew, angry / confused / scam-sounding replies.
- Blacklist: messages appearing to be from Jen Maguire / Jennifer M.
  Dwyer, anyone at Blue Homes Group, or James W. Sofroney Jr. get **no
  reply** — they are flagged to William instead.
- Lead/conversation state lives in a JSON file under `DATA_DIR`
  (default `./data/leads.json`). No database.
- **Voice calls** (`voice/`): inbound calls on the same Telnyx number
  are answered by a Telnyx Voice AI Assistant ("Listing Pixl",
  synthetic Telnyx Ultra voice "Clara" — never a clone of William's
  voice). It gives pricing, takes name/property/details one or two
  questions at a time, offers a callback, and logs the lead via an
  async `log_lead` tool (`POST /tools/log-lead`) plus end-of-call
  insights (`POST /webhooks/telnyx/insights`); William gets one summary
  text per call. If the assistant can't start, or `USE_VOICE_AI=false`,
  a plain Call Control IVR fallback answers instead (greeting + pricing
  menu, gather, take a message). Call events arrive at
  `POST /webhooks/telnyx/voice`.
- Note from the Nov 5 Telnyx probe (see `TELNYX_RESEARCH.md`): the
  Telnyx account is a trial account, balance was -$0.01, and Voice AI
  Assistants are blocked (error 10038) until the account is
  verified/upgraded. A 609 long code also needs 10DLC brand + campaign
  registration before texting real customers (unregistered: ~10 msg/min
  throttle + carrier filtering).

## Pricing quoted (HDPhotoHub is source of truth)

Essential $200 · Standard $290 · Marketing $389 · Luxury $549 ·
Signature $815. Add-ons: drone $145, video $265, Matterport $185,
floor plan $90, twilight $35/photo, staging $35/photo, marketing kit
$39. Minimum $250/shoot. Vacation-rental/Airbnb shoot $800/property.
Retired prices (Essential $295 / Showcase $475 / Premiere $750) are
never quoted. The $250 Jason Ruffenach loyalty rate is never advertised
and is not applied by this version.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values; never commit .env
uvicorn app.main:app --reload --port 8000
curl http://localhost:8000/health
```

Without `TELNYX_API_KEY` / `TELNYX_BOT_NUMBER` the server still runs
and answers webhooks, but outbound SMS is skipped with a warning
(check the logs). For local webhook testing, expose the server
(e.g. `cloudflared tunnel` / ngrok) and point Telnyx at
`{PUBLIC_BASE_URL}/webhooks/telnyx/sms`, or POST sample payloads
directly — see `tests/`.

## Environment variables

| Var | Required | Notes |
| --- | --- | --- |
| `TELNYX_API_KEY` | to send | Telnyx API key |
| `TELNYX_BOT_NUMBER` | to send | Bot number, E.164 |
| `WILLIAM_CELL` | no | Default `+16097807027` |
| `PUBLIC_BASE_URL` | no | Default `http://localhost:8000` |
| `TELNYX_PUBLIC_KEY` | prod: yes | Base64 Ed25519 public key; unset = unsigned accepted (testing only) |
| `TELNYX_ASSISTANT_ID` | voice | Assistant id from `voice/create_assistant.py`; empty = IVR fallback |
| `TELNYX_VOICE_ID` | no | Telnyx Ultra voice UUID override (Clara) |
| `USE_VOICE_AI` | no | Default `true`; `false` = IVR fallback |
| `DATA_DIR` | no | Default `./data` |
| `LOG_LEVEL` | no | Default `INFO` |

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Tests use FastAPI's `TestClient` with a fake Telnyx client — $0, no
network. They cover the greeting → pricing answer, the full lead
capture flow to completion (plus the summary texted to William), STOP
handling, blacklist handling (no reply), malformed payloads (no crash,
HTTP 200), signature verification (valid → 200, tampered → 401),
outside-area leads, and talk-to-Will handoff; plus the voice side —
call.initiated → answer with assistant id, IVR fallback, log-lead tool,
insights/conversation-ended handling, and the assistant config contents.
46 tests total (18 SMS + 28 voice).

## Layout

```
app/
  main.py          app factory, /health, routers (SMS + voice)
  config.py        settings from env vars
  pricing.py       pricing data + reply templates + blacklist
  sms.py           SMS webhook handler + conversation engine + signature check
  telnyx_client.py send SMS via POST https://api.telnyx.com/v2/messages
  state.py         JSON-file lead/conversation store (./data/)
voice/
  assistant_config.json  Telnyx AI Assistant definition (create at launch)
  create_assistant.py    launch-time assistant creation script (Legion)
  webhooks.py      voice router: call events, log-lead tool, insights
  ivr_fallback.py  Call Control IVR fallback
  call_control.py  Telnyx Call Control actions client
  sms_sender.py    standalone SMS sender (voice summaries to William)
  config.py / store.py / leads.py / signatures.py
  tests/           pytest, TestClient, fake Telnyx HTTP + SMS
tests/             pytest, TestClient, fake Telnyx client (SMS)
deploy/
  deploy-legion.ps1  Legion deploy: sync code, venv, NSSM service,
                     tunnel ingress, /health check
```

## Launch checklist

See `LAUNCH.md`.
