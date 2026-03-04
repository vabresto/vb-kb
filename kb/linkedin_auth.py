from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time


def normalize_totp_secret(secret: str) -> str:
    return "".join(ch for ch in secret.strip().upper() if ch.isalnum())


def generate_totp_code(
    *,
    secret: str,
    for_time: int | float | None = None,
    period_seconds: int = 30,
    digits: int = 6,
) -> str:
    normalized_secret = normalize_totp_secret(secret)
    if not normalized_secret:
        raise RuntimeError("linkedin totp secret is empty")
    if digits <= 0:
        raise RuntimeError("totp digits must be positive")
    if period_seconds <= 0:
        raise RuntimeError("totp period_seconds must be positive")

    try:
        key = base64.b32decode(normalized_secret, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("linkedin totp secret must be valid base32") from exc

    timestamp = int(time.time() if for_time is None else for_time)
    counter = int(timestamp // period_seconds)
    counter_bytes = struct.pack(">Q", counter)
    digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (
        ((digest[offset] & 0x7F) << 24)
        | (digest[offset + 1] << 16)
        | (digest[offset + 2] << 8)
        | digest[offset + 3]
    )
    return str(code_int % (10**digits)).zfill(digits)

