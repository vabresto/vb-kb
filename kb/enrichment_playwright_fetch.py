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
from kb.enrichment_playwright_timing import RandomWaitSettings, parse_random_wait_settings, wait_random_delay

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
_LINKEDIN_EXPERIENCE_SELECTORS = (
    "section[id*='experience'] li",
    "section[data-section='experience'] li",
    "main section:has(h2:has-text('Experience')) li",
)
_SKOOL_PROFILE_ENTRY_SELECTORS = (
    "main li",
    "main article li",
    "main section li",
)
_EXPERIENCE_IGNORED_PREFIXES = (
    "show all",
    "see all",
)
_SKOOL_IGNORED_PREFIXES = (
    "show more",
    "show less",
    "see more",
    "see less",
    "view profile",
)


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
    metadata: dict[str, Any] | None = None,
) -> None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return
    merged_metadata = {
        "extractor": "playwright-default",
    }
    if metadata:
        merged_metadata.update(metadata)
    facts.append(
        {
            "attribute": attribute,
            "value": normalized,
            "confidence": confidence,
            "metadata": merged_metadata,
        }
    )


def _normalize_experience_entry(raw_value: str) -> str | None:
    lines = [_normalize_optional_text(line) for line in raw_value.splitlines()]
    cleaned = [line for line in lines if line is not None]
    if not cleaned:
        return None
    candidate = " | ".join(cleaned[:4])
    lowered = candidate.lower()
    if any(lowered.startswith(prefix) for prefix in _EXPERIENCE_IGNORED_PREFIXES):
        return None
    return candidate


def _collect_linkedin_experience_entries(page: Any) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for selector in _LINKEDIN_EXPERIENCE_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 80)
        except Exception:
            continue
        for index in range(count):
            try:
                raw_text = locator.nth(index).inner_text(timeout=2_000)
            except Exception:
                continue
            normalized = _normalize_experience_entry(raw_text)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            entries.append(normalized)
    return entries


def _normalize_skool_entry(raw_value: str) -> str | None:
    lines = [_normalize_optional_text(line) for line in raw_value.splitlines()]
    cleaned = [line for line in lines if line is not None]
    if not cleaned:
        return None
    candidate = " | ".join(cleaned[:4])
    lowered = candidate.lower()
    if any(lowered.startswith(prefix) for prefix in _SKOOL_IGNORED_PREFIXES):
        return None
    if len(candidate) > 280:
        candidate = candidate[:280].rstrip()
    return candidate


def _collect_skool_profile_entries(page: Any) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for selector in _SKOOL_PROFILE_ENTRY_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 120)
        except Exception:
            continue
        for index in range(count):
            try:
                raw_text = locator.nth(index).inner_text(timeout=2_000)
            except Exception:
                continue
            normalized = _normalize_skool_entry(raw_text)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            entries.append(normalized)
    return entries


def _extract_role_company_from_experience(entry: str) -> tuple[str | None, str | None]:
    parts = [_normalize_optional_text(part) for part in entry.split("|")]
    cleaned = [part for part in parts if part is not None]
    if not cleaned:
        return None, None
    role = cleaned[0]
    company = None
    if len(cleaned) > 1:
        company = cleaned[1].split("·", 1)[0].strip()
    return role, _normalize_optional_text(company)


def _deduplicate_text_rows(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduplicated.append(value)
    return deduplicated


def _deduplicate_fact_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for fact in facts:
        attribute = _normalize_optional_text(fact.get("attribute"))
        value = _normalize_optional_text(fact.get("value"))
        confidence = _normalize_optional_text(fact.get("confidence")) or "medium"
        metadata = fact.get("metadata")
        normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if attribute is None or value is None:
            continue
        metadata_blob = json.dumps(normalized_metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        signature = (attribute, value, confidence, metadata_blob)
        if signature in seen:
            continue
        seen.add(signature)
        deduplicated.append(
            {
                "attribute": attribute,
                "value": value,
                "confidence": confidence,
                "metadata": normalized_metadata,
            }
        )
    return deduplicated


def _extract_linkedin_facts(
    *,
    title: str | None,
    description: str | None,
    experience_entries: list[str] | None = None,
) -> list[dict[str, Any]]:
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
    experience_values = [_normalize_optional_text(entry) for entry in (experience_entries or [])]
    normalized_experiences = _deduplicate_text_rows([entry for entry in experience_values if entry is not None])
    for index, entry in enumerate(normalized_experiences):
        _append_fact(
            facts,
            attribute="experience",
            value=entry,
            confidence="low",
            metadata={
                "source_section": "experience",
                "ordinal": index + 1,
            },
        )
    if normalized_experiences:
        role, company = _extract_role_company_from_experience(normalized_experiences[0])
        _append_fact(
            facts,
            attribute="current_role",
            value=role,
            confidence="low",
            metadata={
                "source_section": "experience",
                "inferred": True,
            },
        )
        _append_fact(
            facts,
            attribute="current_company",
            value=company,
            confidence="low",
            metadata={
                "source_section": "experience",
                "inferred": True,
            },
        )
    return facts


def _extract_skool_facts(
    *,
    title: str | None,
    description: str | None,
    profile_entries: list[str] | None = None,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    cleaned_title = _normalize_optional_text(_SKOOL_TITLE_SUFFIX_RE.sub("", title or ""))
    _append_fact(facts, attribute="headline", value=cleaned_title, confidence="medium")
    _append_fact(facts, attribute="about", value=description, confidence="low")
    if cleaned_title is not None:
        community_hint = cleaned_title.split(" - ")[0].strip()
        _append_fact(facts, attribute="community", value=community_hint, confidence="low")
    normalized_entries = _deduplicate_text_rows([entry for entry in (profile_entries or []) if entry])
    for index, entry in enumerate(normalized_entries):
        _append_fact(
            facts,
            attribute="profile_entry",
            value=entry,
            confidence="low",
            metadata={
                "source_section": "profile",
                "ordinal": index + 1,
            },
        )
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


def _scroll_profile(page: Any, wait_settings: RandomWaitSettings) -> None:
    last_height = 0
    stable_steps = 0
    max_steps = 40
    for _ in range(max_steps):
        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(350)
            wait_random_delay(page, wait_settings, minimum_ms=80, maximum_ms=260)
            height = int(page.evaluate("() => document.body.scrollHeight"))
        except Exception:
            break
        if height <= last_height:
            stable_steps += 1
        else:
            stable_steps = 0
        last_height = height
        if stable_steps >= 2:
            break
    page.wait_for_timeout(500)
    wait_random_delay(page, wait_settings, minimum_ms=100, maximum_ms=300)


def _run_fetch(source: SupportedSource) -> int:
    entity_slug = _require_extract_slug()
    cwd = Path.cwd()
    session_state_path = _require_session_path(cwd=cwd)
    headless = _parse_headless(os.environ.get("KB_ENRICHMENT_EXTRACT_HEADLESS"))
    wait_settings = parse_random_wait_settings()
    target_url = _PROFILE_URLS[source].format(slug=entity_slug)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(session_state_path))
        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1200)
        wait_random_delay(page, wait_settings)
        _scroll_profile(page, wait_settings)
        source_url = _normalize_optional_text(page.url) or target_url
        title = _normalize_optional_text(page.title())
        description = _extract_meta_content(page, "description") or _extract_meta_content(
            page, "og:description"
        )
        experience_entries: list[str] = []
        skool_entries: list[str] = []
        if source == SupportedSource.linkedin:
            experience_entries = _collect_linkedin_experience_entries(page)
        else:
            skool_entries = _collect_skool_profile_entries(page)
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
        facts = _extract_linkedin_facts(
            title=title,
            description=description,
            experience_entries=experience_entries,
        )
    else:
        facts = _extract_skool_facts(
            title=title,
            description=description,
            profile_entries=skool_entries,
        )
    facts = _deduplicate_fact_rows(facts)

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
