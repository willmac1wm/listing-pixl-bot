"""Telnyx SMS sending client.

POST https://api.telnyx.com/v2/messages with Bearer auth.
Body: {"from": <bot number>, "to": <E.164>, "text": <message>}.

The HTTP layer is injectable so tests can run with $0 and no network:
pass an httpx.Client (or anything with a .post(url, json=..., headers=...,
timeout=...) interface) to the constructor.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"


class TelnyxClient:
    def __init__(
        self,
        api_key: str = "",
        bot_number: str = "",
        http_client: Any | None = None,
    ):
        self.api_key = api_key
        self.bot_number = bot_number
        self._http = http_client  # injected in tests
        self._owned_client: httpx.Client | None = None

    def _client(self) -> Any:
        if self._http is not None:
            return self._http
        if self._owned_client is None:
            self._owned_client = httpx.Client(timeout=10.0)
        return self._owned_client

    def send_sms(self, to: str, text: str) -> bool:
        """Send one SMS. Returns True on Telnyx acceptance (2xx)."""
        if not self.api_key or not self.bot_number:
            logger.warning(
                "Telnyx not configured (missing API key or bot number); "
                "NOT sending SMS to %s",
                to,
            )
            return False
        if not to or not text:
            logger.warning("Refusing to send empty SMS (to=%r)", to)
            return False
        try:
            resp = self._client().post(
                TELNYX_MESSAGES_URL,
                json={"from": self.bot_number, "to": to, "text": text},
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
        except Exception as exc:  # network error, timeout, ...
            logger.error("Telnyx send to %s failed: %s", to, exc)
            return False
        if 200 <= resp.status_code < 300:
            logger.info("SMS sent to %s (Telnyx %s)", to, resp.status_code)
            return True
        logger.error(
            "Telnyx send to %s rejected (%s): %s",
            to,
            resp.status_code,
            getattr(resp, "text", "")[:500],
        )
        return False

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None
