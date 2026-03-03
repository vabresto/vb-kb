from __future__ import annotations

import pytest

from kb.enrichment_playwright_bootstrap import _generate_totp_code


def test_generate_totp_code_matches_rfc_vector_for_sha1() -> None:
    # RFC 6238 test secret for SHA-1 ("12345678901234567890" in base32).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert _generate_totp_code(secret=secret, for_time=59, digits=8) == "94287082"


def test_generate_totp_code_accepts_spaced_secret_and_returns_6_digits() -> None:
    secret = "GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ"
    code = _generate_totp_code(secret=secret, for_time=59, digits=6)
    assert code == "287082"


def test_generate_totp_code_rejects_invalid_secret() -> None:
    with pytest.raises(RuntimeError, match="valid base32"):
        _generate_totp_code(secret="invalid-$$$$-secret", for_time=59)
