#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kb.linkedin_auth import generate_totp_code
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_OUTPUT_PATH = Path(".build/enrichment/sessions/linkedin.com/storage-state.json")

TOTP_INPUT_SELECTORS: tuple[str, ...] = (
    "input[name='pin']",
    "input[name='verificationCode']",
    "input[name='verification_code']",
    "input[id*='verification-code']",
    "input[id*='verification_code']",
    "input[autocomplete='one-time-code']",
    "input[inputmode='numeric']",
)

TOTP_SUBMIT_SELECTORS: tuple[str, ...] = (
    "button[type='submit']",
    "button:has-text('Verify')",
    "button:has-text('Submit')",
    "button:has-text('Continue')",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authenticate to LinkedIn and save Playwright storage-state JSON.")
    parser.add_argument("--username", required=True, help="LinkedIn username/email.")
    parser.add_argument("--password", required=True, help="LinkedIn password.")
    parser.add_argument("--totp-secret", required=True, help="Base32 TOTP secret (not 6-digit code).")
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output path for storage state JSON (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument("--headed", action="store_true", help="Run headed browser (requires X server).")
    parser.add_argument(
        "--post-login-wait-seconds",
        type=int,
        default=6,
        help="Seconds to wait after login before saving storage state (default: 6).",
    )
    return parser.parse_args()


def _first_visible_selector(page: object, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0:
                return selector
        except Exception:
            continue
    return None


def _fill_first(page: object, selectors: tuple[str, ...], value: str) -> bool:
    selector = _first_visible_selector(page, selectors)
    if selector is None:
        return False
    page.fill(selector, value)
    return True


def _click_first(page: object, selectors: tuple[str, ...]) -> bool:
    selector = _first_visible_selector(page, selectors)
    if selector is None:
        return False
    page.click(selector)
    return True


def _is_login_url(url: str) -> bool:
    lowered = url.lower()
    return "linkedin.com/uas/login" in lowered or lowered.endswith("/login")


def main() -> int:
    args = _parse_args()
    args.output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(60_000)

        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=90_000)

        if not _fill_first(page, ("#username", "input[name='session_key']", "input[type='email']"), args.username):
            raise RuntimeError("unable to locate LinkedIn username/email input")
        if not _fill_first(page, ("#password", "input[name='session_password']", "input[type='password']"), args.password):
            raise RuntimeError("unable to locate LinkedIn password input")
        if not _click_first(
            page,
            ("button[type='submit']", "button[data-litms-control-urn='login-submit']", "button[aria-label='Sign in']"),
        ):
            raise RuntimeError("unable to locate LinkedIn sign-in button")

        page.wait_for_timeout(2_500)

        totp_selector = _first_visible_selector(page, TOTP_INPUT_SELECTORS)
        if totp_selector is not None:
            code = generate_totp_code(secret=args.totp_secret)
            page.fill(totp_selector, code)
            if not _click_first(page, TOTP_SUBMIT_SELECTORS):
                page.keyboard.press("Enter")
            page.wait_for_timeout(4_000)

        if args.post_login_wait_seconds > 0:
            page.wait_for_timeout(args.post_login_wait_seconds * 1000)

        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeoutError:
            pass

        current_url = page.url
        if _is_login_url(current_url):
            raise RuntimeError(
                "still on LinkedIn login page after credential + TOTP attempt; "
                "credentials may be invalid or challenge flow requires manual intervention"
            )

        context.storage_state(path=str(args.output_path.resolve()))
        payload = {
            "ok": True,
            "output_path": str(args.output_path.resolve()),
            "current_url": current_url,
            "title": page.title(),
        }
        print(json.dumps(payload))

        context.close()
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

