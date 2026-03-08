#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kb.linkedin_daemon_client import LinkedInDaemonClient
from kb.linkedin_people_search import (
    canonical_profile_url,
    clean_name,
    normalize_space,
    parse_degree,
    parse_mutuals,
    parse_title_org_from_card,
)

DEFAULT_DAEMON_URL = "http://127.0.0.1:8771"
DEFAULT_PLAN = Path("linkedin_people_search_plan.csv")
DEFAULT_OUTPUT = Path("linkedin_people_search_results_raw.csv")
DEFAULT_PROGRESS = Path("linkedin_people_search_results_raw.progress.json")
DEFAULT_WAIT_MIN = 60
DEFAULT_WAIT_MAX = 600
DEFAULT_RETRY_COUNT = 3

OUTPUT_FIELDS = (
    "captured_at",
    "query_id",
    "theme_line",
    "title_permutation",
    "base_context",
    "location",
    "degree_filter",
    "keywords",
    "query_params_json",
    "search_url_canonical",
    "page_in_query",
    "rank_on_page",
    "name",
    "connection_degree",
    "title",
    "org",
    "location_text",
    "linkedin_url",
    "named_mutuals",
    "mutual_total",
    "all_text",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run full LinkedIn people-search sweeps from a plan CSV. "
            "Scrapes all results cards on each page (search page only), logs progress, and commits/pushes per page."
        )
    )
    parser.add_argument("--plan-csv", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--progress-log", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--wait-min-seconds", type=int, default=DEFAULT_WAIT_MIN)
    parser.add_argument("--wait-max-seconds", type=int, default=DEFAULT_WAIT_MAX)
    parser.add_argument("--retry-count", type=int, default=DEFAULT_RETRY_COUNT)
    parser.add_argument("--commit-prefix", default="data")
    parser.add_argument(
        "--dedupe-mode",
        choices=("none", "global"),
        default="none",
        help="none=append all results; global=skip profile URLs already present in output.",
    )
    return parser.parse_args()


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
    existing = [path for path in files_to_add if path.exists()]
    if not existing:
        return _run_git(repo_root, ["rev-parse", "HEAD"])

    _run_git(repo_root, ["add", *[str(path.relative_to(repo_root)) for path in existing]])
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


def _ensure_output_exists(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()


def _load_seen_urls(path: Path) -> tuple[set[str], int]:
    seen: set[str] = set()
    total_rows = 0
    if not path.exists():
        return seen, total_rows
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total_rows += 1
            profile_url = canonical_profile_url(row.get("linkedin_url", ""))
            if profile_url:
                seen.add(profile_url)
    return seen, total_rows


def _append_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        for row in rows:
            writer.writerow(row)


def _load_plan(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"plan CSV not found: {path}")
    rows: list[dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            enabled = str(row.get("enabled", "true")).strip().lower()
            if enabled not in {"true", "1", "yes", "y"}:
                continue
            rows.append({key: str(value or "") for key, value in row.items()})
    if not rows:
        raise RuntimeError("plan CSV has no enabled rows")
    return rows


def _default_progress(*, output_path: Path, plan_path: Path) -> dict[str, Any]:
    return {
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "running",
        "plan_csv": str(plan_path.resolve()),
        "output_csv": str(output_path.resolve()),
        "rows_written": 0,
        "pages_scanned": 0,
        "query_index": 0,
        "page_in_query": 0,
        "query_id": "",
        "keywords": "",
        "search_url": "",
        "search_title": "",
        "commits": [],
        "parse_errors": [],
    }


def _load_progress(progress_path: Path, *, output_path: Path, plan_path: Path) -> dict[str, Any]:
    if not progress_path.exists():
        return _default_progress(output_path=output_path, plan_path=plan_path)
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_progress(output_path=output_path, plan_path=plan_path)
    if not isinstance(payload, dict):
        return _default_progress(output_path=output_path, plan_path=plan_path)
    payload.setdefault("started_at", _utc_now())
    payload["updated_at"] = _utc_now()
    payload["plan_csv"] = str(plan_path.resolve())
    payload["output_csv"] = str(output_path.resolve())
    payload.setdefault("rows_written", 0)
    payload.setdefault("pages_scanned", 0)
    payload.setdefault("query_index", 0)
    payload.setdefault("page_in_query", 0)
    payload.setdefault("query_id", "")
    payload.setdefault("keywords", "")
    payload.setdefault("search_url", "")
    payload.setdefault("search_title", "")
    payload.setdefault("commits", [])
    payload.setdefault("parse_errors", [])
    payload["status"] = "running"
    return payload


def _save_progress(progress_path: Path, progress: dict[str, Any]) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress["updated_at"] = _utc_now()
    progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")


def _is_bot_challenge(url: str, title: str) -> bool:
    text = normalize_space(f"{url} {title}").lower()
    markers = ("/checkpoint/", "captcha", "security verification", "challenge", "authwall")
    return any(marker in text for marker in markers)


def _command_open_search(
    client: LinkedInDaemonClient,
    *,
    query: str,
    query_params_json: str,
) -> dict[str, Any]:
    query_params: dict[str, Any] | None = None
    if query_params_json.strip():
        try:
            parsed = json.loads(query_params_json)
            if isinstance(parsed, dict):
                query_params = parsed
        except json.JSONDecodeError:
            query_params = None
    return client.command(cmd="open_people_search", params={"query": query, "query_params": query_params or {}})


def main() -> int:
    args = _parse_args()
    if args.retry_count <= 0:
        raise SystemExit("retry-count must be > 0")
    if args.wait_min_seconds <= 0 or args.wait_max_seconds <= 0:
        raise SystemExit("wait min/max must be > 0")
    if args.wait_min_seconds > args.wait_max_seconds:
        raise SystemExit("wait-min-seconds cannot exceed wait-max-seconds")

    repo_root = Path(__file__).resolve().parents[1]
    plan_path = args.plan_csv.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    progress_path = args.progress_log.expanduser().resolve()

    _ensure_output_exists(output_path)
    plan_rows = _load_plan(plan_path)
    seen_urls, total_existing = _load_seen_urls(output_path)
    dedupe_globally = args.dedupe_mode == "global"
    rows_written = total_existing

    progress = _load_progress(progress_path, output_path=output_path, plan_path=plan_path)
    progress["rows_written"] = rows_written
    _save_progress(progress_path, progress)

    client = LinkedInDaemonClient(base_url=args.daemon_url, timeout_seconds=120.0)
    client.wait_until_ready(timeout_seconds=20.0)
    auth = client.command(cmd="assert_authenticated")
    if not bool(auth.get("authenticated")):
        raise SystemExit("LinkedIn daemon session is not authenticated")

    resume_query_index = int(progress.get("query_index") or 0)
    resume_page_in_query = int(progress.get("page_in_query") or 0)
    pages_scanned = int(progress.get("pages_scanned") or 0)
    bot_challenge = False

    for query_index, row in enumerate(plan_rows):
        if query_index < resume_query_index:
            continue

        query_id = row.get("query_id", f"Q{query_index + 1:03d}")
        keywords = row.get("keywords", "").strip()
        if not keywords:
            progress["parse_errors"].append(
                {"type": "plan_error", "query_index": query_index, "query_id": query_id, "error": "empty keywords"}
            )
            _save_progress(progress_path, progress)
            continue

        max_pages = int(row.get("max_pages", "0") or 0)
        if max_pages <= 0:
            max_pages = 120

        next_page_to_scan = 1
        if query_index == resume_query_index and resume_page_in_query > 0:
            next_page_to_scan = resume_page_in_query + 1

        open_payload = _command_open_search(
            client,
            query=keywords,
            query_params_json=row.get("query_params_json", ""),
        )
        current_url = str(open_payload.get("url") or "")
        current_title = str(open_payload.get("title") or "")
        if _is_bot_challenge(current_url, current_title):
            bot_challenge = True
            progress["status"] = "bot_challenge"
            progress["query_index"] = query_index
            progress["query_id"] = query_id
            progress["keywords"] = keywords
            progress["search_url"] = current_url
            progress["search_title"] = current_title
            progress["bot_challenge_at"] = _utc_now()
            _save_progress(progress_path, progress)
            break

        if next_page_to_scan > 1:
            for _ in range(1, next_page_to_scan):
                moved = client.command(cmd="next_page")
                current_url = str(moved.get("url") or current_url)
                current_title = str(moved.get("title") or current_title)
                if not bool(moved.get("moved")):
                    break

        page_in_query = next_page_to_scan
        while page_in_query <= max_pages:
            current = client.command(cmd="current_page")
            current_url = str(current.get("url") or "")
            current_title = str(current.get("title") or "")
            if _is_bot_challenge(current_url, current_title):
                bot_challenge = True
                progress["status"] = "bot_challenge"
                progress["query_index"] = query_index
                progress["query_id"] = query_id
                progress["page_in_query"] = page_in_query
                progress["keywords"] = keywords
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
                    cards = [item for item in raw_cards if isinstance(item, dict)] if isinstance(raw_cards, list) else []
                    scraped_ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    progress["parse_errors"].append(
                        {
                            "type": "scrape_error",
                            "query_index": query_index,
                            "query_id": query_id,
                            "page_in_query": page_in_query,
                            "attempt": attempt,
                            "error": str(exc),
                            "at": _utc_now(),
                        }
                    )
                    _save_progress(progress_path, progress)
                    time.sleep(random.randint(10, 25))
                    open_payload = _command_open_search(
                        client,
                        query=keywords,
                        query_params_json=row.get("query_params_json", ""),
                    )
                    current_url = str(open_payload.get("url") or current_url)
                    current_title = str(open_payload.get("title") or current_title)
                    for _ in range(1, page_in_query):
                        moved = client.command(cmd="next_page")
                        current_url = str(moved.get("url") or current_url)
                        current_title = str(moved.get("title") or current_title)
                        if not bool(moved.get("moved")):
                            break

            if bot_challenge:
                break

            if not scraped_ok:
                progress["parse_errors"].append(
                    {
                        "type": "scrape_failed",
                        "query_index": query_index,
                        "query_id": query_id,
                        "page_in_query": page_in_query,
                        "error": "failed after retries",
                        "at": _utc_now(),
                    }
                )
                _save_progress(progress_path, progress)

            rows_to_append: list[dict[str, str]] = []
            for rank, card in enumerate(cards, start=1):
                raw_href = str(card.get("href") or "")
                profile_url = canonical_profile_url(raw_href)
                if not profile_url:
                    continue
                if dedupe_globally and profile_url in seen_urls:
                    continue

                name = clean_name(str(card.get("name") or ""), profile_url)
                degree = parse_degree(str(card.get("degree") or "")) or parse_degree(str(card.get("all_text") or ""))
                subtitle = str(card.get("subtitle") or "")
                all_text = str(card.get("all_text") or "")
                title, org = parse_title_org_from_card(name=name, subtitle=subtitle, all_text=all_text)
                location_text = str(card.get("location") or "")
                named_mutuals, mutual_total = parse_mutuals(str(card.get("mutual_line") or ""))
                rows_to_append.append(
                    {
                        "captured_at": _utc_now(),
                        "query_id": query_id,
                        "theme_line": row.get("theme_line", ""),
                        "title_permutation": row.get("title_permutation", ""),
                        "base_context": row.get("base_context", ""),
                        "location": row.get("location", ""),
                        "degree_filter": row.get("degree_filter", ""),
                        "keywords": keywords,
                        "query_params_json": row.get("query_params_json", ""),
                        "search_url_canonical": row.get("search_url", ""),
                        "page_in_query": str(page_in_query),
                        "rank_on_page": str(rank),
                        "name": name,
                        "connection_degree": degree,
                        "title": title,
                        "org": org,
                        "location_text": location_text,
                        "linkedin_url": profile_url,
                        "named_mutuals": named_mutuals,
                        "mutual_total": str(mutual_total),
                        "all_text": all_text,
                    }
                )
                if dedupe_globally:
                    seen_urls.add(profile_url)

            if rows_to_append:
                _append_rows(output_path, rows_to_append)
                rows_written += len(rows_to_append)

            pages_scanned += 1
            progress["pages_scanned"] = pages_scanned
            progress["rows_written"] = rows_written
            progress["query_index"] = query_index
            progress["query_id"] = query_id
            progress["keywords"] = keywords
            progress["page_in_query"] = page_in_query
            progress["search_url"] = current_url
            progress["search_title"] = current_title
            progress["last_page_new_rows"] = len(rows_to_append)
            progress["status"] = "running"
            _save_progress(progress_path, progress)

            commit_message = (
                f"{args.commit_prefix}: plan-run {query_id} page {page_in_query} "
                f"(+{len(rows_to_append)}, total {rows_written})"
            )
            commit_sha = _commit_and_push(
                repo_root=repo_root,
                files_to_add=[output_path, progress_path],
                message=commit_message,
                retry_count=args.retry_count,
            )
            progress["commits"].append(
                {
                    "sha": commit_sha,
                    "query_index": query_index,
                    "query_id": query_id,
                    "page_in_query": page_in_query,
                    "new_rows": len(rows_to_append),
                    "total_rows": rows_written,
                    "at": _utc_now(),
                }
            )
            _save_progress(progress_path, progress)
            print(
                f"page_scanned query={query_id} page={page_in_query} added={len(rows_to_append)} "
                f"total={rows_written} commit={commit_sha[:12]}",
                flush=True,
            )

            wait_seconds = random.randint(args.wait_min_seconds, args.wait_max_seconds)
            progress["status"] = "sleeping_between_pages"
            progress["last_wait_seconds"] = wait_seconds
            _save_progress(progress_path, progress)
            print(f"sleeping_seconds={wait_seconds}", flush=True)
            time.sleep(wait_seconds)
            progress["status"] = "running"
            _save_progress(progress_path, progress)

            moved = client.command(cmd="next_page")
            if not bool(moved.get("moved")):
                break
            page_in_query += 1

        if bot_challenge:
            break

    if bot_challenge:
        print("status=bot_challenge")
        print(f"output_csv={output_path}")
        print(f"progress_log={progress_path}")
        print(f"rows={rows_written}")
        return 3

    progress["status"] = "completed"
    progress["rows_written"] = rows_written
    progress["completed_at"] = _utc_now()
    _save_progress(progress_path, progress)
    _commit_and_push(
        repo_root=repo_root,
        files_to_add=[output_path, progress_path],
        message=f"{args.commit_prefix}: completed plan-run collection ({rows_written} rows)",
        retry_count=args.retry_count,
    )
    print("status=completed")
    print(f"output_csv={output_path}")
    print(f"progress_log={progress_path}")
    print(f"rows={rows_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
