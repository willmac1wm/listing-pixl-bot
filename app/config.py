"""Listing Pixl SMS bot — settings.

All configuration comes from environment variables. No secrets live in
this repo; see .env.example for the full list with placeholder values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """Runtime configuration, read from the environment."""

    # Telnyx API key (API key / token from Mission Control). Required to
    # send SMS; the webhook still accepts inbound messages without it,
    # but replies cannot go out.
    telnyx_api_key: str = ""
    # The Telnyx number the bot texts from, E.164 (e.g. +16095550127).
    telnyx_bot_number: str = ""
    # William's cell — lead summaries and escalation flags go here.
    william_cell: str = "+16097807027"
    # Public base URL of this server, used in docs / LAUNCH.md.
    public_base_url: str = "http://localhost:8000"
    # Base64 Ed25519 public key from Telnyx (Mission Control > Keys &
    # Credentials > Public Key). Optional: when unset, unsigned webhooks
    # are accepted (local testing only) and a warning is logged.
    # When set, webhooks without a valid signature are rejected (401).
    telnyx_public_key: str | None = None
    # Directory holding the JSON lead/conversation store.
    data_dir: Path = Path("./data")
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)


def get_settings() -> Settings:
    """Build Settings from the current environment."""
    public_key = os.environ.get("TELNYX_PUBLIC_KEY") or None
    return Settings(
        telnyx_api_key=os.environ.get("TELNYX_API_KEY", ""),
        telnyx_bot_number=os.environ.get("TELNYX_BOT_NUMBER", ""),
        william_cell=os.environ.get("WILLIAM_CELL", "+16097807027"),
        public_base_url=os.environ.get(
            "PUBLIC_BASE_URL", "http://localhost:8000"
        ),
        telnyx_public_key=public_key,
        data_dir=Path(os.environ.get("DATA_DIR", "./data")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
