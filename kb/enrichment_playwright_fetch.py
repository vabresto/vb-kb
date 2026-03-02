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
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse

from kb.enrichment_config import SupportedSource
from kb.enrichment_playwright_timing import (
    RandomWaitSettings,
    parse_random_wait_settings,
    wait_humanized_delay,
    wait_random_delay,
)
from kb.enrichment_runtime_logging import runtime_log

_PROFILE_URLS: dict[SupportedSource, str] = {
    SupportedSource.linkedin: "https://www.linkedin.com/in/{slug}/",
    SupportedSource.skool: "https://www.skool.com/@{slug}",
}
_LINKEDIN_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*linkedin\s*$", re.IGNORECASE)
_LINKEDIN_TITLE_PREFIX_RE = re.compile(r"^\(\d+\)\s*")
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
_LINKEDIN_HEADLINE_SELECTORS = (
    "main .text-body-medium.break-words",
    "main section .text-body-medium.break-words",
    "section .text-body-medium.break-words",
)
_LINKEDIN_SECTION_SELECTORS = (
    "main section",
    "section[id*='profile']",
)
_LINKEDIN_SECTION_TITLE_SELECTORS = (
    "h2",
    "h3",
)
_LINKEDIN_EXPAND_BUTTON_TEXTS = (
    "Show more",
    "See more",
    "Show all",
    "See all",
)
_LINKEDIN_DETAIL_SECTIONS = (
    "experience",
    "education",
    "publications",
    "skills",
    "languages",
    "honors",
    "certifications",
    "projects",
    "courses",
    "volunteering-experiences",
    "patents",
)
_LINKEDIN_MODAL_ROOT_SELECTORS = (
    ".artdeco-modal",
    "div[role='dialog']",
)
_LINKEDIN_MODAL_CLOSE_SELECTORS = (
    ".artdeco-modal button[aria-label='Dismiss']",
    ".artdeco-modal button[aria-label='Close']",
    ".artdeco-modal button[aria-label*='close' i]",
    ".artdeco-modal__dismiss",
    "div[role='dialog'] button[aria-label='Dismiss']",
    "div[role='dialog'] button[aria-label='Close']",
)
_LINKEDIN_EXPAND_EXCLUDED_TOKENS = (
    "people you may know",
    "recommend",
    "suggested",
    "followers",
    "connections",
    "similar profile",
    "also viewed",
    "discover more",
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
_LINKEDIN_SECTION_IGNORED_PREFIXES = (
    "show all",
    "see all",
    "follow",
    "message",
    "connect",
)
_SKOOL_IGNORED_PREFIXES = (
    "show more",
    "show less",
    "see more",
    "see less",
    "view profile",
)
_MISSING_PROFILE_TEXT_HINTS: dict[SupportedSource, tuple[str, ...]] = {
    SupportedSource.linkedin: (
        "profile not found",
        "page not found",
        "this profile is not available",
        "this profile is unavailable",
        "an exact match was not found",
        "we couldn't find a match",
        "no results found",
    ),
    SupportedSource.skool: (
        "page not found",
        "this page is unavailable",
        "this content isn't available",
        "no users found",
        "couldn't find",
        "not available",
        "404",
    ),
}
_SEARCH_URL_TEMPLATES: dict[SupportedSource, tuple[str, ...]] = {
    SupportedSource.linkedin: (
        "https://www.linkedin.com/search/results/people/?keywords={query}",
        "https://duckduckgo.com/?q=site%3Alinkedin.com%2Fin+{query}",
    ),
    SupportedSource.skool: (
        "https://www.skool.com/search?q={query}",
        "https://duckduckgo.com/?q=site%3Askool.com+%40{query}",
    ),
}


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized


def _wait_with_timing_profile(
    page: Any,
    wait_settings: RandomWaitSettings,
    *,
    minimum_ms: int | None = None,
    maximum_ms: int | None = None,
    humanize: bool = False,
) -> int:
    if humanize:
        return wait_humanized_delay(
            page,
            wait_settings,
            minimum_ms=minimum_ms,
            maximum_ms=maximum_ms,
            allow_actions=True,
        )
    return wait_random_delay(
        page,
        wait_settings,
        minimum_ms=minimum_ms,
        maximum_ms=maximum_ms,
    )


def _wait_linkedin_action_delay(
    page: Any,
    wait_settings: RandomWaitSettings,
    *,
    minimum_ms: int | None = None,
    maximum_ms: int | None = None,
) -> int:
    return _wait_with_timing_profile(
        page,
        wait_settings,
        minimum_ms=minimum_ms,
        maximum_ms=maximum_ms,
        humanize=True,
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


def _normalize_linkedin_title(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    normalized = _LINKEDIN_TITLE_SUFFIX_RE.sub("", normalized)
    normalized = _LINKEDIN_TITLE_PREFIX_RE.sub("", normalized)
    return _normalize_optional_text(normalized)


def _normalize_linkedin_profile_headline(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    return _LINKEDIN_TITLE_PREFIX_RE.sub("", normalized).strip()


def _clean_repeated_segments(parts: list[str]) -> list[str]:
    cleaned: list[str] = []
    for part in parts:
        normalized = _normalize_optional_text(part)
        if normalized is None:
            continue
        if cleaned and cleaned[-1] == normalized:
            continue
        cleaned.append(normalized)
    return cleaned


def _normalize_experience_entry(raw_value: str) -> str | None:
    lines = [_normalize_optional_text(line) for line in raw_value.splitlines()]
    cleaned = _clean_repeated_segments([line for line in lines if line is not None])
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


def _extract_first_text(page: Any, selectors: tuple[str, ...], *, timeout_ms: int = 1_500) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            value = _normalize_optional_text(locator.inner_text(timeout=timeout_ms))
            if value is not None:
                return value
        except Exception:
            continue
    return None


def _normalize_linkedin_section_heading(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    cleaned = normalized.rstrip(":")
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    return _normalize_optional_text(cleaned)


def _normalize_linkedin_section_entry(raw_value: str) -> str | None:
    lines = [_normalize_optional_text(line) for line in raw_value.splitlines()]
    cleaned = _clean_repeated_segments([line for line in lines if line is not None])
    if not cleaned:
        return None
    candidate = " | ".join(cleaned[:6])
    lowered = candidate.lower()
    if any(lowered.startswith(prefix) for prefix in _LINKEDIN_SECTION_IGNORED_PREFIXES):
        return None
    if len(candidate) > 420:
        candidate = candidate[:420].rstrip()
    return candidate


def _collect_linkedin_section_entries(page: Any) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for section_selector in _LINKEDIN_SECTION_SELECTORS:
        try:
            section_locator = page.locator(section_selector)
            section_count = min(section_locator.count(), 40)
        except Exception:
            continue
        for section_index in range(section_count):
            section = section_locator.nth(section_index)
            section_title = _extract_first_text(section, _LINKEDIN_SECTION_TITLE_SELECTORS, timeout_ms=1_000)
            normalized_title = _normalize_linkedin_section_heading(section_title)
            if normalized_title is None:
                continue
            try:
                item_locator = section.locator("li")
                item_count = min(item_locator.count(), 40)
            except Exception:
                continue
            for item_index in range(item_count):
                try:
                    raw_text = item_locator.nth(item_index).inner_text(timeout=1_500)
                except Exception:
                    continue
                normalized_entry = _normalize_linkedin_section_entry(raw_text)
                if normalized_entry is None:
                    continue
                value = f"{normalized_title} | {normalized_entry}"
                if value in seen:
                    continue
                seen.add(value)
                entries.append(value)
    return entries


def _has_visible_linkedin_modal(page: Any) -> bool:
    for selector in _LINKEDIN_MODAL_ROOT_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 4)
        except Exception:
            continue
        for index in range(count):
            try:
                if locator.nth(index).is_visible(timeout=200):
                    return True
            except Exception:
                continue
    return False


def _close_linkedin_modal_if_present(page: Any, wait_settings: RandomWaitSettings) -> bool:
    if not _has_visible_linkedin_modal(page):
        return False

    for _ in range(3):
        clicked_close = False
        for selector in _LINKEDIN_MODAL_CLOSE_SELECTORS:
            try:
                control = page.locator(selector).first
                if control.count() == 0:
                    continue
                if not control.is_visible(timeout=300):
                    continue
                control.click(timeout=1_000)
                clicked_close = True
                break
            except Exception:
                continue
        if not clicked_close:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        try:
            page.wait_for_timeout(220)
        except Exception:
            pass
        _wait_linkedin_action_delay(page, wait_settings, minimum_ms=70, maximum_ms=180)
        if not _has_visible_linkedin_modal(page):
            return True
    return True


def _control_text_candidates(control: Any) -> str:
    chunks: list[str] = []
    for attribute in ("aria-label", "id", "data-control-name"):
        try:
            value = _normalize_optional_text(control.get_attribute(attribute, timeout=250))
        except Exception:
            value = None
        if value is not None:
            chunks.append(value)
    try:
        text = _normalize_optional_text(control.inner_text(timeout=250))
    except Exception:
        text = None
    if text is not None:
        chunks.append(text)
    return " ".join(chunks).lower()


def _should_skip_linkedin_expand_control(control: Any, href: str | None) -> bool:
    text_blob = _control_text_candidates(control)
    if any(token in text_blob for token in _LINKEDIN_EXPAND_EXCLUDED_TOKENS):
        return True

    if href is None:
        return False
    normalized_href = href.lower()
    if "/details/" in normalized_href:
        return True
    if "/overlay/" in normalized_href:
        return True
    if "pymk" in normalized_href or "recommendation" in normalized_href or "miniprofileurn" in normalized_href:
        return True
    if normalized_href and not normalized_href.startswith("#"):
        return True
    return False


def _try_click_expand_control(page: Any, locator: Any, wait_settings: RandomWaitSettings) -> bool:
    _close_linkedin_modal_if_present(page, wait_settings)
    try:
        if not locator.is_visible(timeout=500):
            return False
    except Exception:
        return False
    try:
        locator.scroll_into_view_if_needed(timeout=500)
    except Exception:
        pass
    try:
        locator.click(timeout=1_500)
    except Exception:
        return False
    try:
        page.wait_for_timeout(250)
    except Exception:
        pass
    _wait_linkedin_action_delay(page, wait_settings, minimum_ms=70, maximum_ms=170)
    if _has_visible_linkedin_modal(page):
        _close_linkedin_modal_if_present(page, wait_settings)
        return False
    return True


def _expand_linkedin_profile_sections(page: Any, wait_settings: RandomWaitSettings) -> None:
    seen_controls: set[str] = set()
    for _ in range(4):
        _close_linkedin_modal_if_present(page, wait_settings)
        clicked = 0
        for section_selector in _LINKEDIN_SECTION_SELECTORS:
            try:
                section_locator = page.locator(section_selector)
                section_count = min(section_locator.count(), 30)
            except Exception:
                continue
            for section_index in range(section_count):
                section = section_locator.nth(section_index)
                for button_text in _LINKEDIN_EXPAND_BUTTON_TEXTS:
                    selector = f"button:has-text('{button_text}')"
                    try:
                        locator = section.locator(selector)
                        count = min(locator.count(), 30)
                    except Exception:
                        continue
                    for index in range(count):
                        control = locator.nth(index)
                        href = None
                        try:
                            href = control.get_attribute("href", timeout=250)
                        except Exception:
                            href = None
                        key = f"{section_selector}|{section_index}|{selector}|{index}|{href or ''}"
                        if key in seen_controls:
                            continue
                        if _should_skip_linkedin_expand_control(control, href):
                            seen_controls.add(key)
                            continue
                        if not _try_click_expand_control(page, control, wait_settings):
                            continue
                        seen_controls.add(key)
                        clicked += 1
                        try:
                            page.wait_for_timeout(300)
                        except Exception:
                            pass
                        _wait_linkedin_action_delay(page, wait_settings, minimum_ms=80, maximum_ms=220)
        if clicked == 0:
            break


def _linkedin_profile_slug_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "linkedin.com" not in host:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "in":
        return None
    slug = _normalize_optional_text(parts[1])
    if slug is None:
        return None
    return slug.lower()


def _normalize_linkedin_detail_url(url: str, *, profile_slug: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "linkedin.com" not in host:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4:
        return None
    if parts[0].lower() != "in":
        return None
    slug = parts[1].strip().lower()
    if slug != profile_slug:
        return None
    if parts[2].lower() != "details":
        return None
    section = parts[3].strip().lower()
    if section not in _LINKEDIN_DETAIL_SECTIONS:
        return None
    return f"https://www.linkedin.com/in/{slug}/details/{section}/"


def _collect_linkedin_detail_urls(page: Any, *, profile_url: str) -> list[str]:
    profile_slug = _linkedin_profile_slug_from_url(profile_url)
    if profile_slug is None:
        return []

    urls: list[str] = []
    seen: set[str] = set()
    try:
        locator = page.locator("a[href*='/details/']")
        count = min(locator.count(), 200)
    except Exception:
        return []
    for index in range(count):
        try:
            href = locator.nth(index).get_attribute("href", timeout=500)
        except Exception:
            continue
        normalized_href = _normalize_optional_text(href)
        if normalized_href is None:
            continue
        resolved = urljoin(profile_url, normalized_href)
        detail_url = _normalize_linkedin_detail_url(resolved, profile_slug=profile_slug)
        if detail_url is None or detail_url in seen:
            continue
        seen.add(detail_url)
        urls.append(detail_url)
    return urls


def _detail_section_label_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4:
        return "Details"
    section = parts[3].strip().replace("-", " ")
    if not section:
        return "Details"
    return section.title()


def _normalize_linkedin_detail_entry(raw_value: str) -> str | None:
    lines = [_normalize_optional_text(line) for line in raw_value.splitlines()]
    cleaned = _clean_repeated_segments([line for line in lines if line is not None])
    if not cleaned:
        return None
    candidate = " | ".join(cleaned[:8])
    lowered = candidate.lower()
    if any(lowered.startswith(prefix) for prefix in _LINKEDIN_SECTION_IGNORED_PREFIXES):
        return None
    if len(candidate) > 520:
        candidate = candidate[:520].rstrip()
    return candidate


def _collect_linkedin_detail_entries(
    page: Any,
    *,
    detail_urls: list[str],
    wait_settings: RandomWaitSettings,
) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for detail_url in detail_urls[:8]:
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(900)
            try:
                page.wait_for_load_state("networkidle", timeout=7_000)
            except Exception:
                pass
            _wait_linkedin_action_delay(page, wait_settings)
            _close_linkedin_modal_if_present(page, wait_settings)
            _scroll_profile(page, wait_settings, humanize=True)
        except Exception:
            continue
        heading = _extract_first_text(page, ("main h1", "main h2", "h1", "h2"), timeout_ms=1_200)
        normalized_heading = _normalize_linkedin_section_heading(heading) or _detail_section_label_from_url(detail_url)
        for selector in ("main li", "main section li"):
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 180)
            except Exception:
                continue
            for index in range(count):
                try:
                    raw_text = locator.nth(index).inner_text(timeout=1_500)
                except Exception:
                    continue
                normalized = _normalize_linkedin_detail_entry(raw_text)
                if normalized is None:
                    continue
                value = f"{normalized_heading} | {normalized}"
                if value in seen:
                    continue
                seen.add(value)
                entries.append(value)
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
    profile_headline: str | None = None,
    experience_entries: list[str] | None = None,
    section_entries: list[str] | None = None,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    cleaned_title = _normalize_linkedin_title(title)
    cleaned_profile_headline = _normalize_linkedin_profile_headline(profile_headline)
    headline_value = cleaned_profile_headline or cleaned_title
    headline_company: str | None = None
    _append_fact(facts, attribute="headline", value=headline_value, confidence="medium")
    _append_fact(facts, attribute="about", value=description, confidence="low")
    if headline_value is not None:
        company_match = _COMPANY_HINT_RE.search(headline_value)
        if company_match is not None:
            headline_company = company_match.group(1)
            _append_fact(
                facts,
                attribute="current_company",
                value=headline_company,
                confidence="medium",
                metadata={
                    "source_section": "headline",
                    "inferred": True,
                },
            )
    experience_values = [_normalize_optional_text(entry) for entry in (experience_entries or [])]
    normalized_experiences = _deduplicate_text_rows([entry for entry in experience_values if entry is not None])
    for index, entry in enumerate(normalized_experiences):
        _append_fact(
            facts,
            attribute="experience",
            value=entry,
            confidence="medium",
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
            confidence="medium",
            metadata={
                "source_section": "experience",
                "inferred": True,
            },
        )
        normalized_headline_company = _normalize_optional_text(headline_company.lower()) if headline_company else None
        normalized_experience_company = _normalize_optional_text(company.lower()) if company else None
        if normalized_experience_company is not None and normalized_experience_company != normalized_headline_company:
            _append_fact(
                facts,
                attribute="current_company",
                value=company,
                confidence="medium",
                metadata={
                    "source_section": "experience",
                    "inferred": True,
                },
            )
    normalized_sections = _deduplicate_text_rows([entry for entry in (section_entries or []) if entry])
    for index, entry in enumerate(normalized_sections):
        _append_fact(
            facts,
            attribute="section_entry",
            value=entry,
            confidence="low",
            metadata={
                "source_section": "profile_sections",
                "ordinal": index + 1,
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


def _is_profile_url(*, source: SupportedSource, url: str) -> bool:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if source == SupportedSource.linkedin:
        return "linkedin.com" in host and path.startswith("/in/")
    return "skool.com" in host and path.startswith("/@")


def _profile_resolution_reason(*, source: SupportedSource, url: str, title: str | None, html: str) -> str | None:
    normalized_url = _normalize_optional_text(url) or ""
    parsed = urlparse(normalized_url.lower())
    host = parsed.netloc
    visible_text = _visible_text_from_html(html[:12000])
    title_text = _normalize_optional_text(title) or ""
    text_signal = f"{title_text.lower()} {visible_text}".strip()

    if any(hint in text_signal for hint in _MISSING_PROFILE_TEXT_HINTS[source]):
        return f"{source.value} profile not found"

    source_token = source.value.lower().split(".", 1)[0]
    if source_token in host and not _is_profile_url(source=source, url=normalized_url):
        return f"did not land on a {source.value} profile page"
    return None


def _search_query_from_slug(entity_slug: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", entity_slug.lower())
    if not tokens:
        return entity_slug
    return " ".join(tokens)


def _unwrap_search_redirect_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "duckduckgo.com" not in host:
        return url
    query = parse_qs(parsed.query)
    for key in ("uddg", "u", "url"):
        values = query.get(key)
        if not values:
            continue
        target = _normalize_optional_text(values[0])
        if target is not None:
            return unquote(target)
    return url


def _canonical_profile_url(source: SupportedSource, url: str, *, base_url: str | None = None) -> str | None:
    candidate = _unwrap_search_redirect_url(url)
    resolved = urljoin(base_url or "", candidate)
    parsed = urlparse(resolved)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    if source == SupportedSource.linkedin:
        if "linkedin.com" not in host or not path.lower().startswith("/in/"):
            return None
        if not path.endswith("/"):
            path = f"{path}/"
    else:
        if "skool.com" not in host or not path.lower().startswith("/@"):
            return None
        path = path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", "", ""))


def _score_profile_candidate(*, source: SupportedSource, url: str, entity_slug: str) -> int:
    parsed = urlparse(url.lower())
    path = parsed.path
    tokens = re.findall(r"[a-z0-9]+", entity_slug.lower())
    slug_with_hyphens = "-".join(tokens)

    score = 0
    if source == SupportedSource.linkedin and path.startswith("/in/"):
        score += 60
    if source == SupportedSource.skool and path.startswith("/@"):
        score += 60
    if slug_with_hyphens and slug_with_hyphens in path:
        score += 120
    for token in tokens:
        if token in path:
            score += 12
    score -= min(len(path), 160) // 4
    return score


def _select_best_profile_candidate(
    *,
    source: SupportedSource,
    entity_slug: str,
    candidates: list[str],
) -> str | None:
    normalized_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _canonical_profile_url(source, candidate)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        normalized_candidates.append(normalized)
    if not normalized_candidates:
        return None
    ranked = sorted(
        normalized_candidates,
        key=lambda candidate: (_score_profile_candidate(source=source, url=candidate, entity_slug=entity_slug), candidate),
        reverse=True,
    )
    return ranked[0]


def _collect_profile_candidates_from_page(page: Any, *, source: SupportedSource) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    try:
        locator = page.locator("a[href]")
        count = min(locator.count(), 320)
    except Exception:
        return candidates

    for index in range(count):
        try:
            href = locator.nth(index).get_attribute("href", timeout=1_000)
        except Exception:
            continue
        normalized_href = _normalize_optional_text(href)
        if normalized_href is None:
            continue
        normalized_candidate = _canonical_profile_url(source, normalized_href, base_url=page.url)
        if normalized_candidate is None or normalized_candidate in seen:
            continue
        seen.add(normalized_candidate)
        candidates.append(normalized_candidate)
    return candidates


def _scroll_search_results(
    page: Any,
    wait_settings: RandomWaitSettings,
    *,
    humanize: bool = False,
) -> None:
    for _ in range(3):
        try:
            page.evaluate("() => window.scrollBy(0, Math.max(800, window.innerHeight))")
            page.wait_for_timeout(250)
            _wait_with_timing_profile(
                page,
                wait_settings,
                minimum_ms=70,
                maximum_ms=220,
                humanize=humanize,
            )
        except Exception:
            break


def _discover_profile_url(
    *,
    page: Any,
    source: SupportedSource,
    entity_slug: str,
    wait_settings: RandomWaitSettings,
) -> str | None:
    query = quote_plus(_search_query_from_slug(entity_slug))
    for template in _SEARCH_URL_TEMPLATES[source]:
        search_url = template.format(query=query)
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(900)
            _wait_with_timing_profile(page, wait_settings, humanize=source == SupportedSource.linkedin)
            _scroll_search_results(page, wait_settings, humanize=source == SupportedSource.linkedin)
        except Exception:
            continue
        candidates = _collect_profile_candidates_from_page(page, source=source)
        selected = _select_best_profile_candidate(
            source=source,
            entity_slug=entity_slug,
            candidates=candidates,
        )
        if selected is not None:
            return selected
    return None


def _scroll_profile(
    page: Any,
    wait_settings: RandomWaitSettings,
    *,
    humanize: bool = False,
) -> None:
    last_height = 0
    stable_steps = 0
    max_steps = 40
    for _ in range(max_steps):
        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(350)
            _wait_with_timing_profile(
                page,
                wait_settings,
                minimum_ms=80,
                maximum_ms=260,
                humanize=humanize,
            )
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
    _wait_with_timing_profile(
        page,
        wait_settings,
        minimum_ms=100,
        maximum_ms=300,
        humanize=humanize,
    )


def _capture_profile_payload(
    *,
    page: Any,
    source: SupportedSource,
    url: str,
    wait_settings: RandomWaitSettings,
) -> dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1200)
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    _wait_with_timing_profile(page, wait_settings, humanize=source == SupportedSource.linkedin)
    _scroll_profile(page, wait_settings, humanize=source == SupportedSource.linkedin)

    source_url = _normalize_optional_text(page.url) or url
    title = _normalize_optional_text(page.title())
    description = _extract_meta_content(page, "description") or _extract_meta_content(page, "og:description")
    profile_headline = None
    experience_entries: list[str] = []
    skool_entries: list[str] = []
    section_entries: list[str] = []
    html = page.content()
    if source == SupportedSource.linkedin:
        _close_linkedin_modal_if_present(page, wait_settings)
        _expand_linkedin_profile_sections(page, wait_settings)
        _close_linkedin_modal_if_present(page, wait_settings)
        profile_headline = _extract_first_text(page, _LINKEDIN_HEADLINE_SELECTORS)
        experience_entries = _collect_linkedin_experience_entries(page)
        section_entries = _collect_linkedin_section_entries(page)
        html = page.content()
        detail_urls = _collect_linkedin_detail_urls(page, profile_url=source_url)
        if detail_urls:
            _log_runtime(
                f"Captured {len(detail_urls)} LinkedIn detail page link(s); collecting expanded section data."
            )
        detail_entries = _collect_linkedin_detail_entries(
            page,
            detail_urls=detail_urls,
            wait_settings=wait_settings,
        )
        if detail_entries:
            _log_runtime(f"Captured {len(detail_entries)} LinkedIn detail section entries.")
        section_entries = _deduplicate_text_rows(section_entries + detail_entries)
    else:
        skool_entries = _collect_skool_profile_entries(page)
        html = page.content()
    return {
        "source_url": source_url,
        "title": title,
        "description": description,
        "profile_headline": profile_headline,
        "experience_entries": experience_entries,
        "section_entries": section_entries,
        "skool_entries": skool_entries,
        "html": html,
    }


def _log_runtime(message: str) -> None:
    runtime_log("playwright-fetch", message)


def _register_debug_hooks(page: Any) -> tuple[list[str], list[str]]:
    console_errors: list[str] = []
    page_errors: list[str] = []

    def _on_console(message: Any) -> None:
        try:
            level = str(getattr(message, "type", "log")).lower()
        except Exception:
            level = "log"
        if level not in {"error", "warning"}:
            return
        try:
            raw_text = getattr(message, "text", None)
            text = raw_text() if callable(raw_text) else raw_text
        except Exception:
            text = None
        normalized = _normalize_optional_text(text)
        if normalized is None:
            return
        if normalized not in console_errors:
            console_errors.append(normalized)

    def _on_page_error(exc: Exception) -> None:
        normalized = _normalize_optional_text(str(exc))
        if normalized is None:
            return
        if normalized not in page_errors:
            page_errors.append(normalized)

    try:
        page.on("console", _on_console)
    except Exception:
        pass
    try:
        page.on("pageerror", _on_page_error)
    except Exception:
        pass
    return console_errors, page_errors


def _wait_for_manual_intervention(page: Any, *, headless: bool, reason: str) -> None:
    if headless:
        return
    _log_runtime(
        f"Human intervention may be required ({reason}). "
        "Holding browser open for 15 seconds so you can inspect the page."
    )
    try:
        page.wait_for_timeout(15_000)
    except Exception:
        return


def _run_fetch(source: SupportedSource) -> int:
    entity_slug = _require_extract_slug()
    cwd = Path.cwd()
    session_state_path = _require_session_path(cwd=cwd)
    headless = _parse_headless(os.environ.get("KB_ENRICHMENT_EXTRACT_HEADLESS"))
    wait_settings = parse_random_wait_settings()
    target_url = _PROFILE_URLS[source].format(slug=entity_slug)

    from playwright.sync_api import sync_playwright

    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(session_state_path))
        page = context.new_page()
        console_errors, page_errors = _register_debug_hooks(page)
        profile_payload = _capture_profile_payload(
            page=page,
            source=source,
            url=target_url,
            wait_settings=wait_settings,
        )
        source_url = str(profile_payload["source_url"])
        title = _normalize_optional_text(profile_payload.get("title"))
        description = _normalize_optional_text(profile_payload.get("description"))
        profile_headline = _normalize_optional_text(profile_payload.get("profile_headline"))
        experience_entries = list(profile_payload.get("experience_entries") or [])
        section_entries = list(profile_payload.get("section_entries") or [])
        skool_entries = list(profile_payload.get("skool_entries") or [])
        html = str(profile_payload["html"])

        challenge_reason = _unsupported_reason(source=source, url=source_url, title=title, html=html)
        if challenge_reason is not None:
            _log_runtime(f"{source.value} extraction requires intervention: {challenge_reason}")
            if console_errors:
                _log_runtime("Browser console warnings/errors detected during extraction:")
                for entry in console_errors[:8]:
                    _log_runtime(f"  console: {entry}")
            for entry in page_errors[:8]:
                _log_runtime(f"  pageerror: {entry}")
            _wait_for_manual_intervention(page, headless=headless, reason=challenge_reason)
            browser.close()
            payload = {
                "status": "unsupported",
                "reason": challenge_reason,
                "source_url": source_url,
                "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "console_errors": console_errors[:20],
                "page_errors": page_errors[:20],
                "html": html,
            }
            print(json.dumps(payload))
            return 0

        resolution_reason = _profile_resolution_reason(source=source, url=source_url, title=title, html=html)
        if resolution_reason is not None:
            discovered_url = _discover_profile_url(
                page=page,
                source=source,
                entity_slug=entity_slug,
                wait_settings=wait_settings,
            )
            if discovered_url is not None:
                profile_payload = _capture_profile_payload(
                    page=page,
                    source=source,
                    url=discovered_url,
                    wait_settings=wait_settings,
                )
                source_url = str(profile_payload["source_url"])
                title = _normalize_optional_text(profile_payload.get("title"))
                description = _normalize_optional_text(profile_payload.get("description"))
                profile_headline = _normalize_optional_text(profile_payload.get("profile_headline"))
                experience_entries = list(profile_payload.get("experience_entries") or [])
                section_entries = list(profile_payload.get("section_entries") or [])
                skool_entries = list(profile_payload.get("skool_entries") or [])
                html = str(profile_payload["html"])
                challenge_reason = _unsupported_reason(source=source, url=source_url, title=title, html=html)
                if challenge_reason is not None:
                    _log_runtime(f"{source.value} extraction requires intervention: {challenge_reason}")
                    if console_errors:
                        _log_runtime("Browser console warnings/errors detected during extraction:")
                        for entry in console_errors[:8]:
                            _log_runtime(f"  console: {entry}")
                    for entry in page_errors[:8]:
                        _log_runtime(f"  pageerror: {entry}")
                    _wait_for_manual_intervention(page, headless=headless, reason=challenge_reason)
                    browser.close()
                    payload = {
                        "status": "unsupported",
                        "reason": challenge_reason,
                        "source_url": source_url,
                        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "console_errors": console_errors[:20],
                        "page_errors": page_errors[:20],
                        "html": html,
                    }
                    print(json.dumps(payload))
                    return 0
                resolution_reason = _profile_resolution_reason(
                    source=source,
                    url=source_url,
                    title=title,
                    html=html,
                )
            if resolution_reason is not None:
                _log_runtime(f"{source.value} extraction requires intervention: {resolution_reason}")
                if console_errors:
                    _log_runtime("Browser console warnings/errors detected during extraction:")
                    for entry in console_errors[:8]:
                        _log_runtime(f"  console: {entry}")
                for entry in page_errors[:8]:
                    _log_runtime(f"  pageerror: {entry}")
                _wait_for_manual_intervention(page, headless=headless, reason=resolution_reason)
                browser.close()
                payload = {
                    "status": "unsupported",
                    "reason": resolution_reason,
                    "source_url": source_url,
                    "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "console_errors": console_errors[:20],
                    "page_errors": page_errors[:20],
                    "html": html,
                }
                print(json.dumps(payload))
                return 0

        browser.close()

    reason = _unsupported_reason(source=source, url=source_url, title=title, html=html)
    if reason is not None:
        payload = {
            "status": "unsupported",
            "reason": reason,
            "source_url": source_url,
            "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "console_errors": console_errors[:20],
            "page_errors": page_errors[:20],
            "html": html,
        }
        print(json.dumps(payload))
        return 0

    if source == SupportedSource.linkedin:
        facts = _extract_linkedin_facts(
            title=title,
            description=description,
            profile_headline=profile_headline,
            experience_entries=experience_entries,
            section_entries=section_entries,
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
        "console_errors": console_errors[:20],
        "page_errors": page_errors[:20],
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
