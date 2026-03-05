#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from kb.linkedin_daemon_client import LinkedInDaemonClient
from kb.linkedin_people_search import (
    SEARCH_QUERIES,
    canonical_profile_url,
    clean_name,
    is_nyc_text,
    parse_degree,
    parse_mutuals,
    parse_org,
)

DEFAULT_OUTPUT_PATH = Path("linkedin_nyc_insurance_icp_2nd_degree.csv")
DEFAULT_TARGET_COUNT = 50
DEFAULT_MAX_PAGES_PER_QUERY = 6
DEFAULT_DAEMON_SCRIPT = Path("scripts/linkedin_playwright_daemon.py")
DEFAULT_DAEMON_URL = "http://127.0.0.1:8771"
DEFAULT_SESSION_STATE = Path(".build/enrichment/sessions/linkedin.com/storage-state.json")
DEFAULT_DAEMON_STATE_PATH = Path(".build/enrichment/daemon/linkedin-daemon-state.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect NYC second-degree insurance ICP LinkedIn people search results into CSV."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--max-pages-per-query", type=int, default=DEFAULT_MAX_PAGES_PER_QUERY)
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--spawn-daemon", action="store_true", help="Spawn daemon locally if not already running.")
    parser.add_argument("--daemon-script", type=Path, default=DEFAULT_DAEMON_SCRIPT)
    parser.add_argument("--session-state", type=Path, default=DEFAULT_SESSION_STATE)
    parser.add_argument("--daemon-state-path", type=Path, default=DEFAULT_DAEMON_STATE_PATH)
    parser.add_argument("--headed", action="store_true", help="Only applies when --spawn-daemon is set.")
    parser.add_argument(
        "--leave-daemon-running",
        action="store_true",
        help="Only applies when --spawn-daemon is set. Do not request daemon shutdown at end.",
    )
    return parser.parse_args()


def _write_csv(output_path: Path, rows: list[dict[str, str | int]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "name",
                "org",
                "connection_degree",
                "linkedin_url",
                "mutual_names",
                "total_mutuals",
            ),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _spawn_daemon(args: argparse.Namespace) -> subprocess.Popen[str]:
    if not args.daemon_script.exists():
        raise RuntimeError(f"daemon script not found: {args.daemon_script}")
    if not args.session_state.exists():
        raise RuntimeError(f"session state file not found: {args.session_state}")

    parsed = urlparse(args.daemon_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8771
    cmd = [
        sys.executable,
        str(args.daemon_script.resolve()),
        "--session-state",
        str(args.session_state.resolve()),
        "--state-path",
        str(args.daemon_state_path.resolve()),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if args.headed:
        cmd.append("--headed")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path.cwd()),
        bufsize=1,
    )
    if proc.stdout is None:
        raise RuntimeError("failed to capture daemon stdout")
    first_line = proc.stdout.readline().strip()
    if not first_line:
        stderr = proc.stderr.read().strip() if proc.stderr is not None else ""
        raise RuntimeError(f"daemon startup failed (no startup payload): {stderr}")
    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid daemon startup payload: {first_line}") from exc
    if not isinstance(payload, dict) or not bool(payload.get("ok")):
        raise RuntimeError(f"daemon startup failed: {payload}")
    return proc


def _collect_profiles(
    *,
    client: LinkedInDaemonClient,
    target_count: int,
    max_pages_per_query: int,
) -> tuple[list[dict[str, str | int]], int]:
    seen_urls: set[str] = set()
    rows: list[dict[str, str | int]] = []
    pages_visited = 0

    auth = client.command(cmd="assert_authenticated")
    if not bool(auth.get("authenticated")):
        raise RuntimeError(
            "LinkedIn session state is not authenticated (redirected to login). "
            "Refresh .build/enrichment/sessions/linkedin.com/storage-state.json and retry."
        )

    for query in SEARCH_QUERIES:
        if len(rows) >= target_count:
            break
        composed_query = f"{query} New York City Metropolitan Area"
        client.command(cmd="open_people_search", params={"query": composed_query})

        for _ in range(max_pages_per_query):
            pages_visited += 1
            payload = client.command(cmd="scrape_people_cards")
            cards = payload.get("cards")
            if not isinstance(cards, list):
                cards = []

            for item in cards:
                if len(rows) >= target_count:
                    break
                if not isinstance(item, dict):
                    continue

                profile_url = canonical_profile_url(str(item.get("href") or ""))
                if not profile_url or profile_url in seen_urls:
                    continue

                degree = parse_degree(str(item.get("degree") or ""))
                if not degree:
                    degree = parse_degree(str(item.get("all_text") or ""))
                if degree.lower() != "2nd":
                    continue

                location_text = str(item.get("location") or "")
                all_text = str(item.get("all_text") or "")
                if not is_nyc_text(location_text) and not is_nyc_text(all_text):
                    continue

                mutual_names, mutual_total = parse_mutuals(str(item.get("mutual_line") or ""))
                rows.append(
                    {
                        "name": clean_name(str(item.get("name") or ""), profile_url),
                        "org": parse_org(str(item.get("subtitle") or "")),
                        "connection_degree": degree,
                        "linkedin_url": profile_url,
                        "mutual_names": mutual_names,
                        "total_mutuals": mutual_total,
                    }
                )
                seen_urls.add(profile_url)

            if len(rows) >= target_count:
                break

            sleep_seconds = random.uniform(3.0, 6.0)
            client.command(cmd="sleep", params={"seconds": sleep_seconds})
            moved = client.command(cmd="next_page")
            if not bool(moved.get("moved")):
                break

    return rows, pages_visited


def main() -> int:
    args = _parse_args()
    if args.target_count <= 0:
        raise SystemExit("target-count must be > 0")
    if args.max_pages_per_query <= 0:
        raise SystemExit("max-pages-per-query must be > 0")

    daemon_proc: subprocess.Popen[str] | None = None
    if args.spawn_daemon:
        daemon_proc = _spawn_daemon(args)

    client = LinkedInDaemonClient(base_url=args.daemon_url)
    try:
        client.wait_until_ready(timeout_seconds=20.0)
        rows, pages_visited = _collect_profiles(
            client=client,
            target_count=args.target_count,
            max_pages_per_query=args.max_pages_per_query,
        )
    except RuntimeError as exc:
        print(f"error={exc}")
        return 2
    finally:
        if daemon_proc is not None and not args.leave_daemon_running:
            try:
                client.shutdown()
            except Exception:
                pass
            try:
                daemon_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()

    output_path = args.output.resolve()
    _write_csv(output_path, rows)
    print(f"output_csv={output_path}")
    print(f"profiles_collected={len(rows)}")
    print(f"pages_visited={pages_visited}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

