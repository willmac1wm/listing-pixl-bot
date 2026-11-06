"""Telnyx webhook signature verification (Ed25519).

Every Telnyx webhook carries:
    telnyx-signature-ed25519  Base64 Ed25519 signature
    telnyx-timestamp          Unix timestamp of signing

The signed message is exactly "{timestamp}|{raw request body}" (raw bytes —
never parse and re-serialize JSON first). The timestamp must be within
300 seconds of now. The public key comes from the Portal
(Mission Control > Account Settings > Keys & Credentials > Public Key);
the API fetch (GET /v2/public_key) is 403 on this account, so copy it
from the Portal into TELNYX_PUBLIC_KEY.

When no public key is configured, verification is skipped and the
request is accepted (local testing only) with a warning logged.
"""

from __future__ import annotations

import base64
import logging
import time

logger = logging.getLogger(__name__)

MAX_TIMESTAMP_SKEW_SECONDS = 300


def verify_signature(
    raw_body: bytes, headers: dict, public_key_b64: str | None
) -> bool:
    """Return True if the webhook signature is valid (or not configured).

    ``headers`` is a case-insensitive mapping (use ``dict(request.headers)``
    lower-cased, or a plain dict — lookups are done case-insensitively).
    """
    if not public_key_b64:
        logger.warning(
            "TELNYX_PUBLIC_KEY not set — accepting unsigned webhook "
            "(local testing only; set the key in production)"
        )
        return True

    lowered = {str(k).lower(): v for k, v in headers.items()}
    signature_b64 = lowered.get("telnyx-signature-ed25519")
    timestamp = lowered.get("telnyx-timestamp")
    if not signature_b64 or not timestamp:
        logger.warning("Webhook missing Telnyx signature headers")
        return False

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        logger.warning("Webhook has a non-numeric telnyx-timestamp: %r", timestamp)
        return False
    if abs(time.time() - ts) > MAX_TIMESTAMP_SKEW_SECONDS:
        logger.warning("Webhook timestamp too old/new: %s", timestamp)
        return False

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64)
        )
        public_key.verify(
            base64.b64decode(signature_b64),
            timestamp.encode() + b"|" + raw_body,
        )
    except Exception as exc:  # bad key, bad signature, bad base64, ...
        logger.warning("Webhook signature verification failed: %s", exc)
        return False
    return True
