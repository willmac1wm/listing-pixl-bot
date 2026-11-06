# Listing Pixl SMS Bot — Launch Checklist

Do these in order. Nothing here buys anything by itself — the number
purchase is a manual, approved step in the Telnyx portal.

## 1. Buy the number (Telnyx portal, with William's approval)

- [ ] Pick the number (609 options found Nov 5, Atlantic City rate
      center — no Cape May-local numbers exist; e.g. (609) 300-0127).
      Confirm the exact checkout price with William first (~$1/mo).
- [ ] Buy it in the Telnyx Mission Control portal. Do NOT port
      William's 7027 T-Mobile line — porting would cancel it. This bot
      needs its own number.
- [ ] Note the number in E.164 form (e.g. `+16093000127`).

## 2. Attach to a messaging profile

- [ ] In Telnyx, create (or pick) a Messaging Profile for Listing Pixl.
- [ ] Attach the new number to that profile.
- [ ] Set the profile's inbound webhook URL to:
      `{PUBLIC_BASE_URL}/webhooks/telnyx/sms`
      (e.g. `https://api.example.com/webhooks/telnyx/sms` — must be
      public HTTPS once deployed; see deployment notes from the parent.)
- [ ] Set a failover webhook URL too, if Telnyx offers one for the profile.
- [ ] **10DLC registration (US long-code A2P — do not skip):** a 609
      local number needs a registered brand + campaign before it can
      text real customers. Unregistered long codes are throttled to
      ~10 messages/min and carrier-filtered. Register in Telnyx
      (sole-proprietor brand path exists; a Business Registration
      Number / EIN has been mandatory on new submissions since
      2026-02-17 — confirm what William needs to supply). Brand ~$4
      one-time, campaign vetting ~$15. Until the campaign is approved,
      expect throttling/filtering on outbound SMS.

## 2b. Account pre-flight (from the Nov 5 Telnyx probe)

- [ ] Check the Telnyx balance — it was **-$0.01** on Nov 5. Top up
      before buying the number or the order will fail.
- [ ] The account is a **trial account**: expect the number purchase
      to prompt an upgrade/verification step, and Voice AI Assistants
      are currently blocked (Telnyx error 10038 "Feature not permitted").
      The SMS bot works regardless; voice stays a stub until the
      account is verified/upgraded.
- [ ] Copy the Ed25519 **public key** from Portal → Account Settings →
      Keys & Credentials → Public Key (the `GET /v2/public_key` API
      returns the same 403 on this account).

## 3. Configure the server

- [ ] Copy `.env.example` to `.env` on the server and fill in:
      `TELNYX_API_KEY`, `TELNYX_BOT_NUMBER` (the new number),
      `WILLIAM_CELL=+16097807027`, `PUBLIC_BASE_URL`,
      `TELNYX_PUBLIC_KEY` (Mission Control > Keys & Credentials >
      Public Key — **required in production** so spoofed webhooks are
      rejected with 401).
- [ ] Start it: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
      (or the deployment's process manager).
- [ ] `curl {PUBLIC_BASE_URL}/health` → `{"ok": true, ...}` and
      `"telnyx_configured": true`, `"signature_verification": true`.

## 4. Test with a real text (William's phone)

- [ ] Text the bot number: `Hi, what are your prices?`
      Expect the price list back within seconds.
- [ ] Walk the full flow: name + selling/rental → address → beds/baths/
      size → package → readiness. Expect a thank-you text, and a lead
      summary text on William's cell (+16097807027).
- [ ] Text `STOP` from a test phone → expect the opt-out confirmation,
      then no further replies. Text `START` to re-opt in.
- [ ] Check the server logs for any errors, and `data/leads.json`
      for the stored conversation.

## 5. Voice launch (same number, after the account is verified)

Do these after §2b (account verification/upgrade + balance top-up),
because the assistant cannot be created on the trial account (403/10038).

- [ ] Resolve the Clara voice UUID: in the Telnyx portal voice library
      (or `GET /v2/text-to-speech/voices`) find Telnyx Ultra "Clara"
      and copy its UUID (`Telnyx.Ultra.<uuid>`). The name in
      `voice/assistant_config.json` is the pick, not a verified ID.
- [ ] On Legion, run the assistant creation script (dry-run first):
      `python voice\create_assistant.py --dry-run`, then without it.
      It reads the live key from `C:\ai-comms-service\.env` and POSTs
      `voice/assistant_config.json` to `/v2/ai/assistants`. Note the
      returned assistant id (`assistant-<uuid>`) and set
      `TELNYX_ASSISTANT_ID` in the server `.env`, then restart the
      service. (`--voice-id` overrides the voice UUID.)
- [ ] In Telnyx, create a Call Control Application "Listing Pixl Voice"
      with webhook URL `{PUBLIC_BASE_URL}/webhooks/telnyx/voice`
      (`webhook_api_version: "2"`), and attach the same 609 number to it
      (`connection_id`). SMS stays on the messaging profile (§2) —
      one number carries both assignments.
- [ ] Set the assistant's `log_lead` tool URL to
      `{PUBLIC_BASE_URL}/tools/log-lead` and its
      `dynamic_variables_webhook_url` to
      `{PUBLIC_BASE_URL}/voice/dynamic-variables` (both are in the
      assistant config; verify they match the deployed base URL).
- [ ] Set the built-in transfer tool destination to William's cell
      (+16097807027) in the portal before going live.
- [ ] Test with a real call to the bot number: expect the greeting,
      ask about pricing, give a name + address, end the call. Expect
      one lead-summary text on William's cell. Check
      `data/voice_calls.json` for the stored call + transcript.
- [ ] IVR fallback check: set `USE_VOICE_AI=false`, restart, call
      again — expect the spoken greeting + pricing menu, then a
      message taken and texted to William. Set it back to `true`.
- [ ] Until the account is verified, calls to the number fall back to
      the IVR path automatically (no assistant id = IVR).

## 6. Go-live notes

- [ ] The bot never sends marketing and never texts first — it only
      replies to inbound texts/calls and sends lead summaries to William.
- [ ] If Telnyx retries a webhook, the handler is idempotent enough:
      duplicate inbound messages re-run the state machine from the
      stored state (a repeated question may be re-asked once).
- [ ] DNS: `bot.listingpixl.com` must be a CNAME to the tunnel
      (`604c31b2-3a81-41cb-acd7-2ee9d9be2243.cfargotunnel.com`) where
      listingpixl.com DNS is managed. The tunnel ingress rule is added
      by `deploy/deploy-legion.ps1`; without the CNAME, Telnyx cannot
      reach the webhooks. (listingpixl.com DNS is currently wildcarded,
      so the explicit CNAME overrides it.)
