"""Telnyx Call Control client (Voice API v2).

Actions used by the voice bot, per TELNYX_RESEARCH.md §3:
    POST /v2/calls/{call_control_id}/actions/answer
    POST /v2/calls/{call_control_id}/actions/speak
    POST /v2/calls/{call_control_id}/actions/gather_using_ai
    POST /v2/calls/{call_control_id}/actions/hangup

The HTTP layer is injectable so tests run with $0 and no network.
Every method returns True on a 2xx response and False otherwise —
Call Control failures are logged, never raised into a webhook handler
(the webhook must still answer 200 to Telnyx promptly).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELNYX_API_BASE = "https://api.telnyx.com/v2"


class TelnyxCallControl:
    """Thin wrapper over the Telnyx Call Control actions API."""

    def __init__(self, api_key: str = "", http_client: Any | None = None):
        self.api_key = api_key
        self._http = http_client  # injected in tests
        self._owned_client: httpx.Client | None = None

    def _client(self) -> Any:
        if self._http is not None:
            return self._http
        if self._owned_client is None:
            self._owned_client = httpx.Client(timeout=10.0)
        return self._owned_client

    def _post_action(
        self, call_control_id: str, action: str, body: dict
    ) -> bool:
        if not self.api_key:
            logger.warning(
                "TELNYX_API_KEY not set; skipping Call Control action %s "
                "for call %s",
                action,
                call_control_id,
            )
            return False
        url = (
            f"{TELNYX_API_BASE}/calls/{call_control_id}/actions/{action}"
        )
        try:
            resp = self._client().post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
        except Exception as exc:
            logger.error(
                "Call Control %s for %s failed: %s", action, call_control_id, exc
            )
            return False
        if 200 <= resp.status_code < 300:
            logger.info(
                "Call Control %s for %s accepted (%s)",
                action,
                call_control_id,
                resp.status_code,
            )
            return True
        logger.error(
            "Call Control %s for %s rejected (%s): %s",
            action,
            call_control_id,
            resp.status_code,
            getattr(resp, "text", "")[:500],
        )
        return False

    # -- actions -------------------------------------------------------------

    def answer_with_assistant(
        self, call_control_id: str, assistant_id: str
    ) -> bool:
        """Answer and attach the stored AI assistant.

        Only ``assistant.id`` is required — the call uses the stored
        assistant configuration. Telnyx warms the assistant up while the
        call is still ringing; do NOT also send ai_assistant_start.
        """
        return self._post_action(
            call_control_id,
            "answer",
            {"assistant": {"id": assistant_id}},
        )

    def answer(self, call_control_id: str) -> bool:
        """Plain answer (IVR fallback path — no assistant attached)."""
        return self._post_action(call_control_id, "answer", {})

    def speak(
        self,
        call_control_id: str,
        text: str,
        voice: str = "female",
        language: str = "en-US",
    ) -> bool:
        """Play text-to-speech on the call (Telnyx synthetic voice)."""
        return self._post_action(
            call_control_id,
            "speak",
            {"payload": text, "voice": voice, "language": language},
        )

    def gather_using_ai(
        self,
        call_control_id: str,
        parameters: dict,
        assistant_instructions: str | None = None,
        language: str = "en",
    ) -> bool:
        """Collect structured info with an AI gather.

        ``parameters`` is a JSON Schema object describing the fields to
        gather (``{"type": "object", "properties": {...},
        "required": [...]}``). Telnyx reports the result on the voice
        webhook as ``call.ai_gather.ended`` (also handled: the legacy
        ``call.gather.ended`` name).
        """
        body: dict[str, Any] = {"parameters": parameters, "language": language}
        if assistant_instructions:
            body["assistant"] = {"instructions": assistant_instructions}
        return self._post_action(
            call_control_id, "gather_using_ai", body
        )

    def hangup(self, call_control_id: str) -> bool:
        return self._post_action(call_control_id, "hangup", {})

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None
