"""Listing Pixl SMS bot — FastAPI app factory."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from voice.webhooks import router as voice_router
from voice.config import get_voice_settings

from .config import Settings, get_settings
from .sms import router as sms_router
from .state import LeadStore, SeenEventStore
from .telnyx_client import TelnyxClient

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    telnyx_client: TelnyxClient | None = None,
) -> FastAPI:
    """Build the app. Tests inject settings + a fake Telnyx client."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="Listing Pixl Bot",
        description=(
            "Inbound SMS auto-reply + voice-call answering and lead capture "
            "for Listing Pixl (Cape May County, NJ real-estate photography) "
            "via Telnyx."
        ),
        version="0.2.0",
    )
    app.state.settings = settings
    app.state.store = LeadStore(settings.data_dir)
    app.state.seen = SeenEventStore(settings.data_dir)
    app.state.telnyx_client = telnyx_client or TelnyxClient(
        api_key=settings.telnyx_api_key,
        bot_number=settings.telnyx_bot_number,
    )

    @app.get("/health")
    async def health():
        voice_settings = get_voice_settings()
        return {
            "ok": True,
            "service": "listing-pixl-bot",
            "telnyx_configured": bool(
                settings.telnyx_api_key and settings.telnyx_bot_number
            ),
            "signature_verification": bool(settings.telnyx_public_key),
            "voice_ai_enabled": voice_settings.use_voice_ai,
            "assistant_configured": voice_settings.assistant_configured,
            "data_dir": str(settings.data_dir),
        }

    app.include_router(sms_router)
    app.include_router(voice_router)
    return app


# Default app instance for `uvicorn app.main:app`.
app = create_app()
