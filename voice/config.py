"""Listing Pixl voice bot — settings.

All configuration comes from environment variables. No secrets live in
this package; see .env.example for the full list with placeholder values.

Env vars:
    TELNYX_API_KEY       Telnyx live API key (Legion C:\\ai-comms-service\\.env).
    TELNYX_BOT_NUMBER    The 609 Telnyx number, E.164 (bought at launch).
    WILLIAM_CELL         William's cell for lead summaries (+16097807027).
    TELNYX_ASSISTANT_ID  Assistant id from create_assistant.py (assistant-<uuid>).
    TELNYX_PUBLIC_KEY    Base64 Ed25519 public key (Portal > Keys & Credentials).
                         Optional for local testing; REQUIRED in production —
                         without it, unsigned webhooks are accepted.
    TELNYX_VOICE_ID      Optional override for the assistant voice
                         (Telnyx.Ultra.<uuid>); used by create_assistant.py.
    USE_VOICE_AI         "true" (default) = answer with the AI assistant;
                         "false" = IVR fallback (plain Call Control TTS/gather).
    PUBLIC_BASE_URL      Public base URL of this server (default
                         https://bot.listingpixl.com). Used for webhook/tool
                         URLs in the assistant config.
    DATA_DIR             Where voice call/lead JSON is stored.
    LOG_LEVEL            Logging level (default INFO).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VoiceSettings:
    """Runtime configuration for the voice bot, read from the environment."""

    telnyx_api_key: str = ""
    telnyx_bot_number: str = ""
    william_cell: str = "+16097807027"
    telnyx_assistant_id: str = ""
    telnyx_public_key: str | None = None
    telnyx_voice_id: str = ""
    use_voice_ai: bool = True
    public_base_url: str = "https://bot.listingpixl.com"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)

    @property
    def assistant_configured(self) -> bool:
        """True when a call can be answered with the AI assistant."""
        return self.use_voice_ai and bool(self.telnyx_assistant_id)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_voice_settings() -> VoiceSettings:
    """Build VoiceSettings from the current environment."""
    return VoiceSettings(
        telnyx_api_key=os.environ.get("TELNYX_API_KEY", ""),
        telnyx_bot_number=os.environ.get("TELNYX_BOT_NUMBER", ""),
        william_cell=os.environ.get("WILLIAM_CELL", "+16097807027"),
        telnyx_assistant_id=os.environ.get("TELNYX_ASSISTANT_ID", ""),
        telnyx_public_key=os.environ.get("TELNYX_PUBLIC_KEY") or None,
        telnyx_voice_id=os.environ.get("TELNYX_VOICE_ID", ""),
        use_voice_ai=_env_bool("USE_VOICE_AI", True),
        public_base_url=os.environ.get(
            "PUBLIC_BASE_URL", "https://bot.listingpixl.com"
        ).rstrip("/"),
        data_dir=Path(os.environ.get("DATA_DIR", "./data")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
