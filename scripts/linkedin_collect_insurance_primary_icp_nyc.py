#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kb.linkedin_daemon_client import LinkedInDaemonClient
from kb.linkedin_people_search import (
    SEARCH_QUERIES,
    canonical_profile_url,
    clean_name,
    is_nyc_text,
    normalize_space,
    parse_degree,
    parse_mutuals,
)

DEFAULT_DAEMON_URL = "http://127.0.0.1:8771"
DEFAULT_TARGET_COUNT = 100
DEFAULT_MAX_PAGES_PER_QUERY = 12
DEFAULT_WAIT_MIN_SECONDS = 60
DEFAULT_WAIT_MAX_SECONDS = 600
DEFAULT_RETRY_COUNT = 3
DEFAULT_OUTPUT_PATH = Path("insurance_primary_icp_nyc_second_degree_results.csv")
DEFAULT_PROGRESS_PATH = Path("insurance_primary_icp_nyc_second_degree_progress.json")

CSV_FIELDS = (
    "name",
    "connection_degree",
    "title",
    "org",
    "linkedin_url",
    "named_mutuals",
    "mutual_total",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect NYC second-degree insurance ICP LinkedIn search results via daemon API, "
            "append rows to CSV, and commit/push after each scanned page."
        )
    )
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--progress-log", type=Path, default=DEFAULT_PROGRESS_PATH)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--max-pages-per-query", type=int, default=DEFAULT_MAX_PAGES_PER_QUERY)
    parser.add_argument("--wait-min-seconds", type=int, default=DEFAULT_WAIT_MIN_SECONDS)
    parser.add_argument("--wait-max-seconds", type=int, default=DEFAULT_WAIT_MAX_SECONDS)
    parser.add_argument("--retry-count", type=int, default=DEFAULT_RETRY_COUNT)
    parser.add_argument("--commit-prefix", default="data")
    return parser.parse_args()


def _split_title_org(subtitle: str, all_text: str) -> tuple[str, str]:
    text = normalize_space(subtitle)
    if re.match(r"^view\s+.+profile$", text, flags=re.IGNORECASE):
        text = ""

    if not text:
        candidates = [normalize_space(part) for part in all_text.split("|")]
        for candidate in candidates:
            if not candidate:
                continue
            lower = candidate.lower()
            if re.search(r"\b[123](?:st|nd|rd)\b", lower):
                continue
            if "degree connection" in lower:
                continue
            if "mutual connection" in lower:
                continue
            if lower in {"follow", "connect", "message", "pending"}:
                continue
            if lower.startswith("view ") and "profile" in lower:
                continue
            if is_nyc_text(candidate):
                continue
            if re.search(r"\bmetropolitan area\b", lower):
                continue
            text = candidate
            break

    if not text:
        return ("", "")

    for sep in (" at ", " @ "):
        idx = text.lower().find(sep)
        if idx > 0:
            title = normalize_space(text[:idx])
            org = normalize_space(text[idx + len(sep) :])
            return (title, org)

    return (text, "")


def _is_target_role(*, title: str, org: str, all_text: str) -> bool:
    text = normalize_space(f"{title} {org} {all_text}").lower()
    if not text:
        return False

    is_leadership = bool(re.search(r"\b(?:director|vp|vice president|head)\b", text))
    has_ops = bool(re.search(r"\b(?:operations?|ops|operational)\b", text))
    has_claims = bool(re.search(r"\bclaims?\b", text))
    has_policy = bool(re.search(r"\bpolicy\b", text))
    has_service = bool(re.search(r"\bservice\b", text))
    has_excellence = bool(re.search(r"\b(?:excellence|operations?\s+excellence|operational\s+excellence)\b", text))
    has_transform = bool(re.search(r"\btransform(?:ation|ational)?\b", text))
    has_regulatory = bool(re.search(r"\bregulatory\b", text))
    has_reporting = bool(re.search(r"\breporting\b", text))
    insurance_context = bool(
        re.search(r"\b(?:insurance|insurer|carrier|tpa|third[- ]party administrator|reinsur|underwrit)\b", text)
    )

    return is_leadership and has_ops and (
        insurance_context
        or has_claims
        or has_policy
        or has_service
        or has_excellence
        or has_transform
        or has_regulatory
        or has_reporting
    )


def _is_bot_challenge(url: str, title: str) -> bool:
    text = normalize_space(f"{url} {title}").lower()
    markers = (
        "/checkpoint/",
        "captcha",
        "security verification",
        "challenge",
        "authwall",
    )
    return any(marker in text for marker in markers)


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _commit_and_push(
    *,
    repo_root: Path,
    files_to_add: list[Path],
    message: str,
    retry_count: int,
) -> str:
    existing_paths = [path for path in files_to_add if path.exists()]
    if not existing_paths:
        return _run_git(repo_root, ["rev-parse", "HEAD"])

    add_args = ["add", *[str(path.relative_to(repo_root)) for path in existing_paths]]
    _run_git(repo_root, add_args)

    cached_diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    if cached_diff.returncode == 0:
        return _run_git(repo_root, ["rev-parse", "HEAD"])

    _run_git(repo_root, ["commit", "-m", message])
    branch = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])

    push_error = ""
    for attempt in range(1, retry_count + 1):
        push = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if push.returncode == 0:
            return _run_git(repo_root, ["rev-parse", "HEAD"])
        push_error = push.stderr.strip() or push.stdout.strip()
        if attempt < retry_count:
            time.sleep(5)
    raise RuntimeError(f"git push failed after {retry_count} attempts: {push_error}")


def _load_existing_rows(output_path: Path) -> tuple[set[str], list[dict[str, str]]]:
    seen_urls: set[str] = set()
    rows: list[dict[str, str]] = []
    if not output_path.exists():
        return seen_urls, rows

    with output_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            url = canonical_profile_url(row.get("linkedin_url", ""))
            if not url:
                continue
            seen_urls.add(url)
            rows.append(
                {
                    "name": row.get("name", ""),
                    "connection_degree": row.get("connection_degree", ""),
                    "title": row.get("title", ""),
                    "org": row.get("org", ""),
                    "linkedin_url": url,
                    "named_mutuals": row.get("named_mutuals", ""),
                    "mutual_total": row.get("mutual_total", ""),
                }
            )
    return seen_urls, rows


def _append_rows(output_path: Path, rows: list[dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = output_path.exists()
    with output_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not file_exists or output_path.stat().st_size == 0:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _ensure_csv_exists(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        return
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()


def _default_progress(*, output_path: Path, target_count: int) -> dict[str, Any]:
    return {
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "running",
        "target_count": target_count,
        "output_csv": str(output_path),
        "collected_count": 0,
        "pages_scanned": 0,
        "query_index": 0,
        "query": "",
        "page_in_query": 0,
        "search_url": "",
        "search_title": "",
        "people_processed": [],
        "parse_errors": [],
        "commits": [],
    }


def _load_progress(progress_path: Path, *, output_path: Path, target_count: int) -> dict[str, Any]:
    if not progress_path.exists():
        return _default_progress(output_path=output_path, target_count=target_count)
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_progress(output_path=output_path, target_count=target_count)
    if not isinstance(payload, dict):
        return _default_progress(output_path=output_path, target_count=target_count)

    payload.setdefault("started_at", _utc_now())
    payload["updated_at"] = _utc_now()
    payload["target_count"] = int(payload.get("target_count") or target_count)
    payload["output_csv"] = str(output_path)
    payload.setdefault("collected_count", 0)
    payload.setdefault("pages_scanned", 0)
    payload.setdefault("query_index", 0)
    payload.setdefault("query", "")
    payload.setdefault("page_in_query", 0)
    payload.setdefault("search_url", "")
    payload.setdefault("search_title", "")
    payload.setdefault("people_processed", [])
    payload.setdefault("parse_errors", [])
    payload.setdefault("commits", [])
    payload["status"] = "running"
    return payload


def _save_progress(progress_path: Path, progress: dict[str, Any]) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress["updated_at"] = _utc_now()
    progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")


def _refresh_to_page(client: LinkedInDaemonClient, composed_query: str, page_number: int) -> tuple[str, str]:
    client.command(cmd="open_people_search", params={"query": composed_query})
    current = client.command(cmd="current_page")
    current_url = str(current.get("url") or "")
    current_title = str(current.get("title") or "")
    if page_number <= 1:
        return (current_url, current_title)
    for _ in range(1, page_number):
        moved = client.command(cmd="next_page")
        current_url = str(moved.get("url") or current_url)
        current_title = str(moved.get("title") or current_title)
        if not bool(moved.get("moved")):
            break
    return (current_url, current_title)


def _progress_person_entry(
    *,
    query: str,
    page_in_query: int,
    name: str,
    linkedin_url: str,
    action: str,
) -> dict[str, str | int]:
    return {
        "query": query,
        "page_in_query": page_in_query,
        "name": name,
        "linkedin_url": linkedin_url,
        "action": action,
        "at": _utc_now(),
    }


def main() -> int:
    args = _parse_args()
    if args.target_count <= 0:
        raise SystemExit("target-count must be > 0")
    if args.max_pages_per_query <= 0:
        raise SystemExit("max-pages-per-query must be > 0")
    if args.wait_min_seconds <= 0 or args.wait_max_seconds <= 0:
        raise SystemExit("wait min/max must be > 0")
    if args.wait_min_seconds > args.wait_max_seconds:
        raise SystemExit("wait-min-seconds cannot exceed wait-max-seconds")
    if args.retry_count <= 0:
        raise SystemExit("retry-count must be > 0")

    repo_root = Path(__file__).resolve().parents[1]
    output_path = args.output.expanduser().resolve()
    progress_path = args.progress_log.expanduser().resolve()
    _ensure_csv_exists(output_path)

    seen_urls, _ = _load_existing_rows(output_path)
    progress = _load_progress(progress_path, output_path=output_path, target_count=args.target_count)
    progress["collected_count"] = len(seen_urls)
    _save_progress(progress_path, progress)

    if len(seen_urls) >= args.target_count:
        progress["status"] = "completed"
        _save_progress(progress_path, progress)
        print(f"already_collected={len(seen_urls)}")
        print(f"output_csv={output_path}")
        print(f"progress_log={progress_path}")
        return 0

    client = LinkedInDaemonClient(base_url=args.daemon_url, timeout_seconds=120.0)
    client.wait_until_ready(timeout_seconds=20.0)
    auth = client.command(cmd="assert_authenticated")
    if not bool(auth.get("authenticated")):
        raise SystemExit("LinkedIn daemon session is not authenticated")

    resume_query_index = int(progress.get("query_index") or 0)
    resume_page_in_query = int(progress.get("page_in_query") or 0)
    pages_scanned = int(progress.get("pages_scanned") or 0)

    done = False
    bot_challenge = False

    for query_index, query in enumerate(SEARCH_QUERIES):
        if query_index < resume_query_index:
            continue

        composed_query = f"{query} New York City Metropolitan Area"
        next_page_to_scan = 1
        if query_index == resume_query_index and resume_page_in_query > 0:
            next_page_to_scan = resume_page_in_query + 1

        current_url, current_title = _refresh_to_page(client, composed_query, next_page_to_scan)
        if _is_bot_challenge(current_url, current_title):
            bot_challenge = True
            progress["status"] = "bot_challenge"
            progress["query_index"] = query_index
            progress["query"] = query
            progress["page_in_query"] = max(next_page_to_scan, 1)
            progress["search_url"] = current_url
            progress["search_title"] = current_title
            progress["bot_challenge_at"] = _utc_now()
            _save_progress(progress_path, progress)
            break

        page_in_query = next_page_to_scan
        while page_in_query <= args.max_pages_per_query:
            current = client.command(cmd="current_page")
            current_url = str(current.get("url") or "")
            current_title = str(current.get("title") or "")
            if _is_bot_challenge(current_url, current_title):
                bot_challenge = True
                progress["status"] = "bot_challenge"
                progress["query_index"] = query_index
                progress["query"] = query
                progress["page_in_query"] = page_in_query
                progress["search_url"] = current_url
                progress["search_title"] = current_title
                progress["bot_challenge_at"] = _utc_now()
                _save_progress(progress_path, progress)
                break

            cards: list[dict[str, Any]] = []
            scraped_ok = False
            for attempt in range(1, args.retry_count + 1):
                try:
                    payload = client.command(cmd="scrape_people_cards")
                    raw_cards = payload.get("cards")
                    if isinstance(raw_cards, list):
                        cards = [item for item in raw_cards if isinstance(item, dict)]
                    else:
                        cards = []
                    scraped_ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    progress["parse_errors"].append(
                        {
                            "type": "scrape_error",
                            "query_index": query_index,
                            "query": query,
                            "page_in_query": page_in_query,
                            "attempt": attempt,
                            "error": str(exc),
                            "at": _utc_now(),
                        }
                    )
                    _save_progress(progress_path, progress)
                    time.sleep(random.randint(10, 25))
                    current_url, current_title = _refresh_to_page(client, composed_query, page_in_query)
                    if _is_bot_challenge(current_url, current_title):
                        bot_challenge = True
                        progress["status"] = "bot_challenge"
                        progress["query_index"] = query_index
                        progress["query"] = query
                        progress["page_in_query"] = page_in_query
                        progress["search_url"] = current_url
                        progress["search_title"] = current_title
                        progress["bot_challenge_at"] = _utc_now()
                        _save_progress(progress_path, progress)
                        break

            if bot_challenge:
                break

            if not scraped_ok:
                progress["parse_errors"].append(
                    {
                        "type": "scrape_failed",
                        "query_index": query_index,
                        "query": query,
                        "page_in_query": page_in_query,
                        "error": "failed after retries",
                        "at": _utc_now(),
                    }
                )
                _save_progress(progress_path, progress)

            rows_to_append: list[dict[str, str]] = []
            for card in cards:
                raw_url = str(card.get("href") or "")
                profile_url = canonical_profile_url(raw_url)
                profile_name = clean_name(str(card.get("name") or ""), profile_url) if profile_url else ""
                if not profile_url:
                    progress["people_processed"].append(
                        _progress_person_entry(
                            query=query,
                            page_in_query=page_in_query,
                            name=profile_name,
                            linkedin_url=raw_url,
                            action="skipped_invalid_url",
                        )
                    )
                    continue

                try:
                    degree = parse_degree(str(card.get("degree") or "")) or parse_degree(str(card.get("all_text") or ""))
                    if degree.lower() != "2nd":
                        progress["people_processed"].append(
                            _progress_person_entry(
                                query=query,
                                page_in_query=page_in_query,
                                name=profile_name,
                                linkedin_url=profile_url,
                                action="skipped_not_2nd",
                            )
                        )
                        continue

                    location_text = str(card.get("location") or "")
                    all_text = str(card.get("all_text") or "")
                    if not is_nyc_text(location_text) and not is_nyc_text(all_text):
                        progress["people_processed"].append(
                            _progress_person_entry(
                                query=query,
                                page_in_query=page_in_query,
                                name=profile_name,
                                linkedin_url=profile_url,
                                action="skipped_not_nyc",
                            )
                        )
                        continue

                    subtitle = str(card.get("subtitle") or "")
                    title, org = _split_title_org(subtitle, all_text)
                    if not _is_target_role(title=title, org=org, all_text=all_text):
                        progress["people_processed"].append(
                            _progress_person_entry(
                                query=query,
                                page_in_query=page_in_query,
                                name=profile_name,
                                linkedin_url=profile_url,
                                action="skipped_role_mismatch",
                            )
                        )
                        continue

                    if profile_url in seen_urls:
                        progress["people_processed"].append(
                            _progress_person_entry(
                                query=query,
                                page_in_query=page_in_query,
                                name=profile_name,
                                linkedin_url=profile_url,
                                action="skipped_duplicate",
                            )
                        )
                        continue

                    named_mutuals, mutual_total = parse_mutuals(str(card.get("mutual_line") or ""))
                    rows_to_append.append(
                        {
                            "name": profile_name,
                            "connection_degree": degree,
                            "title": title,
                            "org": org,
                            "linkedin_url": profile_url,
                            "named_mutuals": named_mutuals,
                            "mutual_total": str(mutual_total),
                        }
                    )
                    seen_urls.add(profile_url)
                    progress["people_processed"].append(
                        _progress_person_entry(
                            query=query,
                            page_in_query=page_in_query,
                            name=profile_name,
                            linkedin_url=profile_url,
                            action="added",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    progress["parse_errors"].append(
                        {
                            "type": "person_parse_error",
                            "query_index": query_index,
                            "query": query,
                            "page_in_query": page_in_query,
                            "linkedin_url": profile_url,
                            "name": profile_name,
                            "error": str(exc),
                            "at": _utc_now(),
                        }
                    )
                    progress["people_processed"].append(
                        _progress_person_entry(
                            query=query,
                            page_in_query=page_in_query,
                            name=profile_name,
                            linkedin_url=profile_url,
                            action="parse_error_skipped",
                        )
                    )

            if rows_to_append:
                _append_rows(output_path, rows_to_append)

            pages_scanned += 1
            progress["pages_scanned"] = pages_scanned
            progress["query_index"] = query_index
            progress["query"] = query
            progress["page_in_query"] = page_in_query
            progress["search_url"] = current_url
            progress["search_title"] = current_title
            progress["last_page_new_rows"] = len(rows_to_append)
            progress["collected_count"] = len(seen_urls)
            progress["status"] = "running"
            _save_progress(progress_path, progress)

            commit_message = (
                f"{args.commit_prefix}: nyc insurance icp page {page_in_query} "
                f"query {query_index + 1}/{len(SEARCH_QUERIES)} (+{len(rows_to_append)}, total {len(seen_urls)})"
            )
            commit_sha = _commit_and_push(
                repo_root=repo_root,
                files_to_add=[output_path, progress_path],
                message=commit_message,
                retry_count=args.retry_count,
            )
            print(
                f"page_scanned query={query_index + 1}/{len(SEARCH_QUERIES)} "
                f"page={page_in_query} added={len(rows_to_append)} total={len(seen_urls)} "
                f"commit={commit_sha[:12]}",
                flush=True,
            )
            progress["commits"].append(
                {
                    "sha": commit_sha,
                    "query_index": query_index,
                    "page_in_query": page_in_query,
                    "new_rows": len(rows_to_append),
                    "total_rows": len(seen_urls),
                    "at": _utc_now(),
                }
            )
            _save_progress(progress_path, progress)

            if len(seen_urls) >= args.target_count:
                done = True
                progress["status"] = "completed"
                progress["completed_at"] = _utc_now()
                progress["collected_count"] = len(seen_urls)
                _save_progress(progress_path, progress)
                _commit_and_push(
                    repo_root=repo_root,
                    files_to_add=[output_path, progress_path],
                    message=f"{args.commit_prefix}: completed nyc insurance icp collection ({len(seen_urls)} rows)",
                    retry_count=args.retry_count,
                )
                break

            wait_seconds = random.randint(args.wait_min_seconds, args.wait_max_seconds)
            progress["last_wait_seconds"] = wait_seconds
            progress["status"] = "sleeping_between_pages"
            _save_progress(progress_path, progress)
            print(f"sleeping_seconds={wait_seconds}", flush=True)
            time.sleep(wait_seconds)
            progress["status"] = "running"
            _save_progress(progress_path, progress)

            moved = client.command(cmd="next_page")
            if not bool(moved.get("moved")):
                break
            page_in_query += 1

        if done or bot_challenge:
            break

    if bot_challenge:
        print("status=bot_challenge")
        print(f"output_csv={output_path}")
        print(f"progress_log={progress_path}")
        print(f"rows={len(seen_urls)}")
        return 3

    if done:
        print("status=completed")
    else:
        progress["status"] = "exhausted_queries"
        progress["collected_count"] = len(seen_urls)
        _save_progress(progress_path, progress)
        _commit_and_push(
            repo_root=repo_root,
            files_to_add=[output_path, progress_path],
            message=f"{args.commit_prefix}: exhausted queries ({len(seen_urls)} rows)",
            retry_count=args.retry_count,
        )
        print("status=exhausted_queries")

    print(f"output_csv={output_path}")
    print(f"progress_log={progress_path}")
    print(f"rows={len(seen_urls)}")
    return 0 if done else 4


if __name__ == "__main__":
    raise SystemExit(main())
