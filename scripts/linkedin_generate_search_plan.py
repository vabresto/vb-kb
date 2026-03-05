#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import urlencode

DEFAULT_OUTPUT = Path("linkedin_people_search_plan.csv")
DEFAULT_LOCATION = "New York City Metropolitan Area"
DEFAULT_CONTEXT = "insurance"
DEFAULT_MAX_PAGES = 120


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic LinkedIn people-search query plan CSV from a topic theme. "
            "The output includes canonical query params/URL (excluding page/session-tracking params)."
        )
    )
    parser.add_argument("--theme-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--base-context", default=DEFAULT_CONTEXT)
    parser.add_argument("--max-pages-per-query", type=int, default=DEFAULT_MAX_PAGES)
    return parser.parse_args()


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def _theme_lines(theme: str) -> list[str]:
    lines: list[str] = []
    for raw in theme.splitlines():
        line = _normalize(raw.lstrip("-*0123456789. ").strip())
        if not line:
            continue
        lowered = line.lower()
        if lowered.endswith(":"):
            continue
        if lowered.startswith("primary buyers"):
            continue
        if not re.search(r"\b(?:director|vp|vice president|head)\b", lowered):
            continue
        lines.append(line)
    return lines


def _strip_parenthetical(text: str) -> str:
    return _normalize(re.sub(r"\([^)]*\)", "", text))


def _split_slash_variants(function_blob: str) -> list[str]:
    parts = [_normalize(part) for part in re.split(r"\s*/\s*", function_blob) if _normalize(part)]
    if not parts:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = _normalize(value)
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        variants.append(normalized)

    for part in parts:
        add(part)

    if len(parts) == 2:
        left, right = parts
        add(f"{left} {right}")
        if re.search(r"\b(?:ops|operations)\b", right, flags=re.IGNORECASE) and not re.search(
            r"\b(?:ops|operations)\b",
            left,
            flags=re.IGNORECASE,
        ):
            add(f"{left} Ops")
            add(f"{left} Operations")

    if len(parts) > 2:
        for index in range(len(parts) - 1):
            add(f"{parts[index]} {parts[index + 1]}")

    return variants


def _expand_line_to_titles(line: str) -> list[str]:
    text = _strip_parenthetical(line)
    lowered = text.lower()

    role_variants: list[str]
    function_blob: str

    match_director_vp = re.match(r"^\s*director\s*/\s*vp\s+(?P<body>.+)$", text, flags=re.IGNORECASE)
    if match_director_vp:
        role_variants = ["Director", "VP", "Vice President"]
        function_blob = _normalize(match_director_vp.group("body"))
    else:
        match_head_of = re.match(r"^\s*head\s+of\s+(?P<body>.+)$", text, flags=re.IGNORECASE)
        if match_head_of:
            role_variants = ["Head of"]
            function_blob = _normalize(match_head_of.group("body"))
        else:
            match_head = re.match(r"^\s*head\s+(?P<body>.+)$", text, flags=re.IGNORECASE)
            if match_head:
                role_variants = ["Head"]
                function_blob = _normalize(match_head.group("body"))
            else:
                role_variants = [""]
                function_blob = text

    function_blob = _normalize(
        re.sub(
            r"\b(?:in|at)\s+(?:insurers?|carriers?|tpas?|third[- ]party administrators?)\b.*$",
            "",
            function_blob,
            flags=re.IGNORECASE,
        )
    )
    function_variants = _split_slash_variants(function_blob) or [function_blob]

    titles: list[str] = []
    seen: set[str] = set()
    for role in role_variants:
        for function in function_variants:
            if not function:
                continue
            if role:
                candidate = f"{role} {function}" if role.lower() != "head of" else f"Head of {function}"
            else:
                candidate = function
            normalized = _normalize(candidate)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            titles.append(normalized)
    return titles


def _canonical_query_params(keywords: str) -> dict[str, str]:
    return {
        "facetNetwork": '["S"]',
        "keywords": keywords,
        "origin": "GLOBAL_SEARCH_HEADER",
    }


def _canonical_search_url(params: dict[str, str]) -> str:
    ordered = [
        ("facetNetwork", params["facetNetwork"]),
        ("keywords", params["keywords"]),
        ("origin", params["origin"]),
    ]
    return f"https://www.linkedin.com/search/results/people/?{urlencode(ordered)}"


def _write_plan(
    *,
    rows: list[dict[str, str]],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "query_id",
                "theme_line",
                "title_permutation",
                "base_context",
                "location",
                "degree_filter",
                "keywords",
                "query_params_json",
                "search_url",
                "max_pages",
                "enabled",
            ),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = _parse_args()
    if args.max_pages_per_query <= 0:
        raise SystemExit("max-pages-per-query must be > 0")
    if not args.theme_file.exists():
        raise SystemExit(f"theme file not found: {args.theme_file}")

    theme_text = args.theme_file.read_text(encoding="utf-8")
    theme_lines = _theme_lines(theme_text)
    if not theme_lines:
        raise SystemExit("theme file produced no valid lines")

    rows: list[dict[str, str]] = []
    query_counter = 1
    for line in theme_lines:
        titles = _expand_line_to_titles(line)
        for title in titles:
            keywords = _normalize(f"{args.base_context} {title} {args.location}")
            params = _canonical_query_params(keywords)
            rows.append(
                {
                    "query_id": f"Q{query_counter:03d}",
                    "theme_line": line,
                    "title_permutation": title,
                    "base_context": args.base_context,
                    "location": args.location,
                    "degree_filter": "2nd",
                    "keywords": keywords,
                    "query_params_json": json.dumps(params, sort_keys=True),
                    "search_url": _canonical_search_url(params),
                    "max_pages": str(args.max_pages_per_query),
                    "enabled": "true",
                }
            )
            query_counter += 1

    _write_plan(rows=rows, output=args.output)
    print(f"output_plan={args.output.resolve()}")
    print(f"queries={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
