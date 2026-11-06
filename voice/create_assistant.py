"""Create the Listing Pixl Telnyx AI Assistant — LAUNCH-TIME ONLY.

Run ON LEGION only, after the Telnyx account is verified/upgraded
(the trial account returns 403 error 10038 on /v2/ai/assistants today).

    python create_assistant.py --dry-run        # print the request body, send nothing
    python create_assistant.py                  # POST to Telnyx, print the assistant id

The API key is read from the TELNYX_API_KEY env var, or from
C:\\ai-comms-service\\.env on Legion (the live key lives there).
The key is NEVER printed, logged, or returned.

Optional overrides:
    --voice-id Telnyx.Ultra.<uuid>   Use a resolved Ultra voice UUID
                                     (also: TELNYX_VOICE_ID env var).
    --base-url https://host          Rewrite the tool / dynamic-variables
                                     URLs (also: PUBLIC_BASE_URL env var).
    --config PATH                    Assistant config JSON (default:
                                     assistant_config.json next to this script).
    --allow-unresolved-voice         Real run only: proceed even if the
                                     voice is not a Telnyx.Ultra.<uuid>.
                                     (Dry-run only warns.)

Exit codes: 0 = created (or dry-run OK), 1 = Telnyx / request error,
2 = missing key or config.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

TELNYX_API_BASE = "https://api.telnyx.com/v2"
ASSISTANTS_URL = f"{TELNYX_API_BASE}/ai/assistants"

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "assistant_config.json"
LEGION_ENV_PATH = Path(r"C:\ai-comms-service\.env")
DEFAULT_BASE_URL = "https://bot.listingpixl.com"

# Telnyx Ultra voice IDs are UUIDs: Telnyx.Ultra.<uuid>.
VOICE_UUID_RE = re.compile(
    r"^Telnyx\.Ultra\.[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
BARE_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def read_api_key(env_file: Path | None = None) -> str:
    """Return the Telnyx API key without ever printing it.

    Order: TELNYX_API_KEY env var, then the Legion .env file.
    """
    key = os.environ.get("TELNYX_API_KEY", "").strip()
    if key:
        return key
    path = env_file or LEGION_ENV_PATH
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "TELNYX_API_KEY":
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return ""


def load_payload(config_path: Path) -> dict:
    """Load assistant_config.json and return ONLY the 'assistant' object."""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read config {config_path}: {exc}", file=sys.stderr)
        sys.exit(2)
    payload = config.get("assistant")
    if not isinstance(payload, dict) or not payload.get("name"):
        print(
            f"ERROR: {config_path} has no valid 'assistant' object",
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def resolve_voice_id(value: str) -> str:
    """Normalise a voice override: bare UUID -> Telnyx.Ultra.<uuid>."""
    value = value.strip()
    if BARE_UUID_RE.match(value):
        return f"Telnyx.Ultra.{value}"
    return value


def apply_overrides(payload: dict, voice_id: str, base_url: str) -> None:
    """Apply --voice-id / --base-url overrides in place."""
    if voice_id:
        payload.setdefault("voice_settings", {})["voice"] = resolve_voice_id(voice_id)
    if base_url and base_url != DEFAULT_BASE_URL:
        old = DEFAULT_BASE_URL
        new = base_url.rstrip("/")
        for tool in payload.get("tools", []):
            url = tool.get("url", "")
            if url.startswith(old):
                tool["url"] = new + url[len(old):]
        dv_url = payload.get("dynamic_variables_webhook_url", "")
        if dv_url.startswith(old):
            payload["dynamic_variables_webhook_url"] = new + dv_url[len(old):]


def voice_warning(payload: dict) -> str | None:
    """Warn when the voice is not a resolved Telnyx.Ultra.<uuid>."""
    voice = (payload.get("voice_settings") or {}).get("voice", "")
    if VOICE_UUID_RE.match(voice or ""):
        return None
    return (
        f"WARNING: voice is {voice!r}, not a Telnyx.Ultra.<uuid>. Telnyx Ultra "
        "voice IDs for AI Assistants are UUIDs — copy the exact UUID for "
        "Clara from the Portal voice library or "
        "GET /v2/text-to-speech/voices, then re-run with --voice-id "
        "Telnyx.Ultra.<uuid> (or set TELNYX_VOICE_ID). The short name may "
        "be rejected by the API."
    )


def create_assistant(payload: dict, api_key: str) -> dict:
    """POST the assistant; return the parsed response JSON."""
    resp = httpx.post(
        ASSISTANTS_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:1000]}
    if resp.status_code in (200, 201):
        return data
    detail = ""
    if isinstance(data, dict):
        errors = data.get("errors") or []
        if errors and isinstance(errors[0], dict):
            detail = (
                f" (code {errors[0].get('code')}: "
                f"{errors[0].get('detail') or errors[0].get('title')})"
            )
    if resp.status_code == 403:
        raise RuntimeError(
            f"Telnyx rejected the create with HTTP 403{detail}. On this "
            "account that means error 10038 'Feature not permitted at this "
            "account level' — the trial account must be verified/upgraded "
            "before Voice AI Assistants can be created."
        )
    raise RuntimeError(
        f"Telnyx rejected the create with HTTP {resp.status_code}{detail}: "
        f"{json.dumps(data)[:1000]}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the Listing Pixl Telnyx AI Assistant (launch-time only)."
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help="assistant config JSON (default: assistant_config.json)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the request body and exit without sending anything.",
    )
    parser.add_argument(
        "--voice-id", default=os.environ.get("TELNYX_VOICE_ID", ""),
        help="Voice override, e.g. Telnyx.Ultra.<uuid> (or TELNYX_VOICE_ID).",
    )
    parser.add_argument(
        "--base-url", default=os.environ.get("PUBLIC_BASE_URL", ""),
        help="Public base URL for tool/dynamic-variables URLs.",
    )
    parser.add_argument(
        "--allow-unresolved-voice", action="store_true",
        help="Real run: proceed even if the voice is not a Telnyx.Ultra.<uuid>.",
    )
    args = parser.parse_args(argv)

    payload = load_payload(args.config)
    apply_overrides(payload, args.voice_id, args.base_url)
    warning = voice_warning(payload)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print("\n--- DRY RUN: nothing was sent to Telnyx. ---")
        if warning:
            print(warning, file=sys.stderr)
        return 0

    if warning:
        print(warning, file=sys.stderr)
        if not args.allow_unresolved_voice:
            print(
                "Refusing to send a create with an unresolved voice. "
                "Re-run with --voice-id Telnyx.Ultra.<uuid> or pass "
                "--allow-unresolved-voice to proceed anyway.",
                file=sys.stderr,
            )
            return 1

    api_key = read_api_key()
    if not api_key:
        print(
            "ERROR: no Telnyx API key found. Set TELNYX_API_KEY, or run on "
            "Legion where C:\\ai-comms-service\\.env holds the live key.",
            file=sys.stderr,
        )
        return 2

    try:
        result = create_assistant(payload, api_key)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    assistant_id = (result.get("data") or {}).get("id")
    print(f"Assistant created: {assistant_id}")
    print("Save this id as TELNYX_ASSISTANT_ID in the server env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
