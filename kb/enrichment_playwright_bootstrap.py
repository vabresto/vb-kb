from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from collections.abc import Sequence
from typing import Any

from kb.enrichment_config import SupportedSource
from kb.enrichment_playwright_timing import RandomWaitSettings, parse_random_wait_settings, wait_random_delay

_LOGIN_URLS: dict[SupportedSource, str] = {
    SupportedSource.linkedin: "https://www.linkedin.com/login",
    SupportedSource.skool: "https://www.skool.com/login",
}
_LINKEDIN_TOTP_INPUT_SELECTORS: tuple[str, ...] = (
    "input[name='pin']",
    "input[name='verificationCode']",
    "input[name='verification_code']",
    "input[id*='verification-code']",
    "input[id*='verification_code']",
    "input[autocomplete='one-time-code']",
    "input[inputmode='numeric']",
)
_LINKEDIN_TOTP_SUBMIT_SELECTORS: tuple[str, ...] = (
    "button[type='submit']",
    "button:has-text('Verify')",
    "button:has-text('Submit')",
    "button:has-text('Continue')",
)


def _parse_headless(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return True


def _manual_wait_seconds(value: str | None) -> int:
    if value is None or not value.strip():
        return 120
    try:
        seconds = int(value)
    except ValueError:
        return 120
    return max(10, min(seconds, 1800))


def _lookup_credential(env_key_name: str | None) -> str | None:
    if env_key_name is None or not env_key_name.strip():
        return None
    return os.environ.get(env_key_name.strip())


def _normalize_totp_secret(secret: str) -> str:
    return "".join(ch for ch in secret.strip().upper() if ch.isalnum())


def _generate_totp_code(
    *,
    secret: str,
    for_time: int | float | None = None,
    period_seconds: int = 30,
    digits: int = 6,
) -> str:
    normalized_secret = _normalize_totp_secret(secret)
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


def _first_visible_selector(page: Any, selectors: Sequence[str]) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0:
                return selector
        except Exception:
            continue
    return None


def _fill_first(page: Any, selectors: Sequence[str], value: str) -> bool:
    selector = _first_visible_selector(page, selectors)
    if selector is None:
        return False
    page.fill(selector, value)
    return True


def _click_first(page: Any, selectors: Sequence[str]) -> bool:
    selector = _first_visible_selector(page, selectors)
    if selector is None:
        return False
    page.click(selector)
    return True


def _attempt_linkedin_login(
    page: Any,
    username: str,
    password: str,
    wait_settings: RandomWaitSettings,
) -> None:
    if not _fill_first(page, ("#username", "input[name='session_key']", "input[type='email']"), username):
        raise RuntimeError("unable to locate LinkedIn username/email input")
    wait_random_delay(page, wait_settings)
    if not _fill_first(page, ("#password", "input[name='session_password']", "input[type='password']"), password):
        raise RuntimeError("unable to locate LinkedIn password input")
    wait_random_delay(page, wait_settings)
    if not _click_first(
        page,
        (
            "button[type='submit']",
            "button[data-litms-control-urn='login-submit']",
            "button[aria-label='Sign in']",
        ),
    ):
        raise RuntimeError("unable to locate LinkedIn sign-in button")
    wait_random_delay(page, wait_settings)


def _linkedin_totp_prompt_visible(page: Any) -> bool:
    return _first_visible_selector(page, _LINKEDIN_TOTP_INPUT_SELECTORS) is not None


def _attempt_linkedin_totp(page: Any, totp_secret: str, wait_settings: RandomWaitSettings) -> None:
    code = _generate_totp_code(secret=totp_secret)
    if not _fill_first(page, _LINKEDIN_TOTP_INPUT_SELECTORS, code):
        raise RuntimeError("unable to locate LinkedIn verification code input")
    wait_random_delay(page, wait_settings)
    if not _click_first(page, _LINKEDIN_TOTP_SUBMIT_SELECTORS):
        page.keyboard.press("Enter")
    wait_random_delay(page, wait_settings)
    page.wait_for_timeout(4_000)
    if _linkedin_totp_prompt_visible(page):
        raise RuntimeError("linkedin verification code was not accepted; check TOTP secret and system clock")


def _attempt_skool_login(
    page: Any,
    username: str,
    password: str,
    wait_settings: RandomWaitSettings,
) -> None:
    if not _fill_first(
        page,
        (
            "input[type='email']",
            "input[name='email']",
            "input[autocomplete='email']",
            "input[type='text']",
        ),
        username,
    ):
        raise RuntimeError("unable to locate Skool username/email input")
    wait_random_delay(page, wait_settings)
    if not _fill_first(
        page,
        (
            "input[type='password']",
            "input[name='password']",
            "input[autocomplete='current-password']",
        ),
        password,
    ):
        raise RuntimeError("unable to locate Skool password input")
    wait_random_delay(page, wait_settings)
    if not _click_first(
        page,
        (
            "button[type='submit']",
            "button:has-text('Sign in')",
            "button:has-text('Log in')",
        ),
    ):
        raise RuntimeError("unable to locate Skool sign-in button")
    wait_random_delay(page, wait_settings)


def _has_source_cookie(cookies: list[dict[str, Any]], source: SupportedSource) -> bool:
    source_token = source.value.lower()
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lower()
        if source_token in domain:
            return True
    return False


def _assert_non_empty_session(storage_state: dict[str, Any], source: SupportedSource) -> None:
    cookies = storage_state.get("cookies")
    origins = storage_state.get("origins")

    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise RuntimeError("bootstrap storageState must include 'cookies' and 'origins' lists")

    if not cookies and not origins:
        raise RuntimeError(
            f"no session data captured for {source.value}; complete login and rerun bootstrap"
        )

    if cookies and _has_source_cookie(cookies, source):
        return

    if origins:
        return

    raise RuntimeError(
        f"no {source.value} cookies captured; complete login and rerun bootstrap"
    )


def _run_bootstrap(source: SupportedSource) -> int:
    headless = _parse_headless(os.environ.get("KB_ENRICHMENT_BOOTSTRAP_HEADLESS"))
    username = _lookup_credential(os.environ.get("KB_ENRICHMENT_BOOTSTRAP_USERNAME_ENV"))
    password = _lookup_credential(os.environ.get("KB_ENRICHMENT_BOOTSTRAP_PASSWORD_ENV"))
    totp_secret = _lookup_credential(os.environ.get("KB_ENRICHMENT_BOOTSTRAP_TOTP_ENV"))
    if source == SupportedSource.linkedin and not totp_secret:
        totp_secret = os.environ.get("KB_ENRICH_LINKEDIN_TOTP_SECRET")
    manual_wait_seconds = _manual_wait_seconds(os.environ.get("KB_ENRICHMENT_BOOTSTRAP_WAIT_SECONDS"))
    wait_settings = parse_random_wait_settings()

    if headless and (not username or not password):
        print(
            (
                "headless bootstrap requires credentials. "
                "Set source credential env vars or rerun with --headful."
            ),
            file=sys.stderr,
        )
        return 2

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(_LOGIN_URLS[source], wait_until="domcontentloaded", timeout=60_000)
        wait_random_delay(page, wait_settings)

        if username and password:
            if source == SupportedSource.linkedin:
                _attempt_linkedin_login(page, username, password, wait_settings)
                page.wait_for_timeout(2_500)
                if _linkedin_totp_prompt_visible(page):
                    if not totp_secret:
                        raise RuntimeError(
                            "linkedin verification code prompt detected but no TOTP secret is configured. "
                            "Set KB_ENRICH_LINKEDIN_TOTP_SECRET (or override via KB_ENRICHMENT_LINKEDIN_TOTP_ENV)."
                        )
                    _attempt_linkedin_totp(page, totp_secret, wait_settings)
            else:
                _attempt_skool_login(page, username, password, wait_settings)
            page.wait_for_timeout(6_000)
        else:
            print(
                (
                    f"Headful bootstrap for {source.value}: complete login in the browser window. "
                    f"Waiting {manual_wait_seconds} seconds before capturing storageState."
                ),
                file=sys.stderr,
            )
            page.wait_for_timeout(manual_wait_seconds * 1000)

        storage_state = context.storage_state()
        browser.close()

    _assert_non_empty_session(storage_state, source)
    print(json.dumps(storage_state))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright bootstrap command for enrichment sessions.")
    parser.add_argument(
        "--source",
        required=True,
        choices=[source.value for source in SupportedSource],
        help="Source domain to bootstrap (linkedin.com or skool.com).",
    )
    args = parser.parse_args()

    source = SupportedSource(args.source)
    try:
        return _run_bootstrap(source)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
