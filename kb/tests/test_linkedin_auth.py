from __future__ import annotations

import pytest

from kb.linkedin_auth import generate_totp_code


def test_generate_totp_code_matches_rfc6238_sha1_vector() -> None:
    # RFC 6238 test secret for SHA-1 ("12345678901234567890" in base32).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert generate_totp_code(secret=secret, for_time=59, digits=8) == "94287082"


def test_generate_totp_code_accepts_spaced_secret() -> None:
    secret = "GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ"
    code = generate_totp_code(secret=secret, for_time=59, digits=6)
    assert code == "287082"


def test_generate_totp_code_rejects_invalid_secret() -> None:
    with pytest.raises(RuntimeError, match="valid base32"):
        generate_totp_code(secret="invalid-$$$$-secret", for_time=59)

