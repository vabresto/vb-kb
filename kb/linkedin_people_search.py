from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

SEARCH_QUERIES: tuple[str, ...] = (
    "insurance director claims operations",
    "insurance vp claims operations",
    "insurance director policy administration",
    "insurance vp policy administration",
    "insurance director service operations",
    "insurance vp service operations",
    "insurance director operations excellence transformation",
    "insurance vp operations excellence transformation",
    "insurance head regulatory reporting operations",
    "tpa director claims operations insurance",
)

NYC_LOCATION_TOKENS: tuple[str, ...] = (
    "new york",
    "nyc",
    "new york city metropolitan area",
    "manhattan",
    "brooklyn",
    "queens",
    "bronx",
    "staten island",
    "jersey city",
    "hoboken",
    "long island city",
)


def normalize_space(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


def canonical_profile_url(url: str | None) -> str:
    text = normalize_space(url)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return ""
    host = parsed.netloc.lower()
    if "linkedin.com" not in host:
        return ""
    path = parsed.path or "/"
    if "/in/" not in path.lower():
        return ""
    if not path.endswith("/"):
        path = f"{path}/"
    return urlunparse(("https", host, path, "", "", ""))


def is_nyc_text(text: str | None) -> bool:
    lowered = normalize_space(text).lower()
    if not lowered:
        return False
    return any(token in lowered for token in NYC_LOCATION_TOKENS)


def clean_name(name: str | None, profile_url: str) -> str:
    cleaned = normalize_space(name)
    if cleaned:
        return cleaned
    parsed = urlparse(profile_url)
    slug = parsed.path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()


def parse_org(subtitle: str | None) -> str:
    text = normalize_space(subtitle)
    if not text:
        return ""
    lowered = text.lower()
    if " at " in lowered:
        _, _, trailing = text.partition(" at ")
        candidate = trailing
    elif " @ " in text:
        _, _, trailing = text.partition(" @ ")
        candidate = trailing
    else:
        candidate = text
    candidate = re.split(r"\s+[|,-]\s+|\s+·\s+", candidate, maxsplit=1)[0]
    return normalize_space(candidate)


def parse_degree(text: str | None) -> str:
    normalized = normalize_space(text)
    if normalized:
        direct = re.search(r"\b([123](?:st|nd|rd))\b", normalized, flags=re.IGNORECASE)
        if direct:
            return direct.group(1)
    return ""


def parse_name_list(fragment: str) -> list[str]:
    text = normalize_space(fragment)
    if not text:
        return []
    text = re.sub(r"\s+and\s+", ", ", text, flags=re.IGNORECASE)
    parts = [normalize_space(part) for part in text.split(",")]
    return [part for part in parts if part]


def parse_mutuals(mutual_line: str | None) -> tuple[str, int]:
    line = normalize_space(mutual_line)
    if not line:
        return ("", 0)

    match_other_count = re.search(
        r"^(?P<names>.+?)\s+and\s+(?P<extra>\d+)\s+other\s+mutual\s+connections?$",
        line,
        flags=re.IGNORECASE,
    )
    if match_other_count:
        names = parse_name_list(match_other_count.group("names"))
        total = len(names) + int(match_other_count.group("extra"))
        return ("; ".join(names), total)

    match_is_single = re.search(r"^(?P<name>.+?)\s+is\s+a\s+mutual\s+connection$", line, flags=re.IGNORECASE)
    if match_is_single:
        names = parse_name_list(match_is_single.group("name"))
        return ("; ".join(names), max(1, len(names)))

    match_are = re.search(r"^(?P<names>.+?)\s+are\s+mutual\s+connections?$", line, flags=re.IGNORECASE)
    if match_are:
        names = parse_name_list(match_are.group("names"))
        return ("; ".join(names), len(names))

    if "mutual connection" in line.lower():
        return (line, 0)
    return ("", 0)

