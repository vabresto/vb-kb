from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kb.enrichment_config import SupportedSource

_PROFILE_URLS: dict[SupportedSource, str] = {
    SupportedSource.linkedin: "https://www.linkedin.com/in/{slug}/",
    SupportedSource.skool: "https://www.skool.com/@{slug}",
}
_LINKEDIN_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*linkedin\s*$", re.IGNORECASE)
_SKOOL_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*skool\s*$", re.IGNORECASE)
_COMPANY_HINT_RE = re.compile(r"\bat\s+([^|,]+)", re.IGNORECASE)
_HTML_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_CAPTCHA_TEXT_HINTS = (
    "verify you are human",
    "confirm you are human",
    "prove you're not a robot",
    "prove you are not a robot",
    "let us know you're not a robot",
    "let us know you are not a robot",
    "security verification",
    "complete the security check",
    "complete this security check",
    "checking your browser before accessing",
    "attention required",
    "just a moment",
)
_CAPTCHA_URL_HINTS = (
    "/checkpoint/challenge",
    "/captcha",
    "/cdn-cgi/challenge-platform",
)
_LINKEDIN_LOGIN_PATH_HINTS = (
    "/login",
    "/uas/login",
    "/checkpoint/lg/login-submit",
    "/authwall",
)
_SKOOL_LOGIN_PATH_HINTS = ("/login",)


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized


def _parse_headless(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return True


def _resolve_source(args_source: str | None) -> SupportedSource:
    source_value = args_source or os.environ.get("KB_ENRICHMENT_EXTRACT_SOURCE")
    if source_value is None or not source_value.strip():
        raise RuntimeError("missing source; pass --source or set KB_ENRICHMENT_EXTRACT_SOURCE")
    return SupportedSource(source_value.strip())


def _require_extract_slug() -> str:
    slug = _normalize_optional_text(os.environ.get("KB_ENRICHMENT_EXTRACT_ENTITY_SLUG"))
    if slug is None:
        raise RuntimeError("KB_ENRICHMENT_EXTRACT_ENTITY_SLUG must be set")
    return slug


def _require_session_path(*, cwd: Path) -> Path:
    raw_path = _normalize_optional_text(os.environ.get("KB_ENRICHMENT_EXTRACT_SESSION_PATH"))
    if raw_path is None:
        raise RuntimeError("KB_ENRICHMENT_EXTRACT_SESSION_PATH must be set")
    path = Path(raw_path)
    resolved = path if path.is_absolute() else cwd / path
    if not resolved.exists():
        raise RuntimeError(f"session state path does not exist: {resolved.as_posix()}")
    return resolved


def _extract_meta_content(page: Any, name: str) -> str | None:
    selectors = (
        f"meta[name='{name}']",
        f"meta[property='{name}']",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            content = locator.get_attribute("content", timeout=1500)
            normalized = _normalize_optional_text(content)
            if normalized is not None:
                return normalized
        except Exception:
            continue
    return None


def _append_fact(
    facts: list[dict[str, Any]],
    *,
    attribute: str,
    value: str | None,
    confidence: str = "medium",
) -> None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return
    facts.append(
        {
            "attribute": attribute,
            "value": normalized,
            "confidence": confidence,
            "metadata": {
                "extractor": "playwright-default",
            },
        }
    )


def _extract_linkedin_facts(*, title: str | None, description: str | None) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    cleaned_title = _normalize_optional_text(_LINKEDIN_TITLE_SUFFIX_RE.sub("", title or ""))
    _append_fact(facts, attribute="headline", value=cleaned_title, confidence="medium")
    _append_fact(facts, attribute="about", value=description, confidence="low")
    if cleaned_title is not None:
        company_match = _COMPANY_HINT_RE.search(cleaned_title)
        if company_match is not None:
            _append_fact(
                facts,
                attribute="current_company",
                value=company_match.group(1),
                confidence="low",
            )
    return facts


def _extract_skool_facts(*, title: str | None, description: str | None) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    cleaned_title = _normalize_optional_text(_SKOOL_TITLE_SUFFIX_RE.sub("", title or ""))
    _append_fact(facts, attribute="headline", value=cleaned_title, confidence="medium")
    _append_fact(facts, attribute="about", value=description, confidence="low")
    if cleaned_title is not None:
        community_hint = cleaned_title.split(" - ")[0].strip()
        _append_fact(facts, attribute="community", value=community_hint, confidence="low")
    return facts


def _visible_text_from_html(html_content: str) -> str:
    without_script = _HTML_SCRIPT_STYLE_RE.sub(" ", html_content)
    without_tags = _HTML_TAG_RE.sub(" ", without_script)
    unescaped = html.unescape(without_tags)
    return _WHITESPACE_RE.sub(" ", unescaped).strip().lower()


def _is_login_page(*, source: SupportedSource, normalized_url: str) -> bool:
    parsed = urlparse(normalized_url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if source == SupportedSource.linkedin:
        if "linkedin.com" not in host:
            return False
        return any(path.startswith(prefix) for prefix in _LINKEDIN_LOGIN_PATH_HINTS)
    if "skool.com" not in host:
        return False
    return any(path.startswith(prefix) for prefix in _SKOOL_LOGIN_PATH_HINTS)


def _is_captcha_challenge(*, normalized_url: str, text_signal: str) -> bool:
    if any(hint in normalized_url for hint in _CAPTCHA_URL_HINTS):
        return True
    if any(hint in text_signal for hint in _CAPTCHA_TEXT_HINTS):
        return True
    if "captcha" in text_signal and any(token in text_signal for token in ("verify", "human", "robot", "security")):
        return True
    return False


def _unsupported_reason(*, source: SupportedSource, url: str, title: str | None, html: str) -> str | None:
    normalized_url = _normalize_optional_text(url) or ""
    visible_text = _visible_text_from_html(html[:12000])
    title_text = _normalize_optional_text(title) or ""
    text_signal = f"{title_text.lower()} {visible_text}".strip()
    if _is_login_page(source=source, normalized_url=normalized_url.lower()):
        if source == SupportedSource.linkedin:
            return "authenticated linkedin session required"
        return "authenticated skool session required"
    if _is_captcha_challenge(normalized_url=normalized_url.lower(), text_signal=text_signal):
        return "captcha challenge detected"
    return None


def _run_fetch(source: SupportedSource) -> int:
    entity_slug = _require_extract_slug()
    cwd = Path.cwd()
    session_state_path = _require_session_path(cwd=cwd)
    headless = _parse_headless(os.environ.get("KB_ENRICHMENT_EXTRACT_HEADLESS"))
    target_url = _PROFILE_URLS[source].format(slug=entity_slug)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(session_state_path))
        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1800)
        source_url = _normalize_optional_text(page.url) or target_url
        title = _normalize_optional_text(page.title())
        description = _extract_meta_content(page, "description") or _extract_meta_content(
            page, "og:description"
        )
        html = page.content()
        browser.close()

    reason = _unsupported_reason(source=source, url=source_url, title=title, html=html)
    if reason is not None:
        payload = {
            "status": "unsupported",
            "reason": reason,
            "source_url": source_url,
            "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "html": html,
        }
        print(json.dumps(payload))
        return 0

    if source == SupportedSource.linkedin:
        facts = _extract_linkedin_facts(title=title, description=description)
    else:
        facts = _extract_skool_facts(title=title, description=description)

    if not facts:
        fallback = title or f"{source.value} profile for {entity_slug}"
        facts = [
            {
                "attribute": "headline",
                "value": fallback,
                "confidence": "low",
                "metadata": {
                    "extractor": "playwright-default",
                    "fallback": True,
                },
            }
        ]

    payload = {
        "source_url": source_url,
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "facts": facts,
        "html": html,
    }
    print(json.dumps(payload))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Default Playwright fetch command for enrichment extraction.")
    parser.add_argument(
        "--source",
        choices=[source.value for source in SupportedSource],
        default=None,
        help="Optional source override (defaults to KB_ENRICHMENT_EXTRACT_SOURCE).",
    )
    args = parser.parse_args()

    try:
        source = _resolve_source(args.source)
        return _run_fetch(source)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
