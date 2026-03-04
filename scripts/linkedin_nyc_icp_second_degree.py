#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
from pathlib import Path

from kb.linkedin_people_search import (
    SEARCH_QUERIES,
    canonical_profile_url,
    clean_name,
    is_nyc_text,
    parse_degree,
    parse_mutuals,
    parse_org,
)

DEFAULT_SESSION_STATE = Path(".build/enrichment/sessions/linkedin.com/storage-state.json")
DEFAULT_OUTPUT_PATH = Path("linkedin_nyc_insurance_icp_2nd_degree.csv")
DEFAULT_TARGET_COUNT = 50
DEFAULT_MAX_PAGES_PER_QUERY = 6
DEFAULT_DAEMON_SCRIPT = Path("scripts/linkedin_playwright_daemon.py")


class DaemonClient:
    def __init__(self, *, daemon_script: Path, session_state: Path, headed: bool) -> None:
        cmd: list[str] = [sys.executable, str(daemon_script.resolve()), "--session-state", str(session_state.resolve())]
        if headed:
            cmd.append("--headed")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(Path.cwd()),
            bufsize=1,
        )
        self._next_id = 1
        startup = self._read_response()
        if not startup.get("ok"):
            raise RuntimeError(f"daemon failed to start: {startup.get('error')}")
        if startup.get("event") != "ready":
            raise RuntimeError(f"unexpected daemon startup payload: {startup}")

    def _read_response(self) -> dict[str, object]:
        if self._proc.stdout is None:
            raise RuntimeError("daemon stdout unavailable")
        line = self._proc.stdout.readline()
        if not line:
            stderr = ""
            if self._proc.stderr is not None:
                stderr = self._proc.stderr.read().strip()
            raise RuntimeError(f"daemon exited unexpectedly; stderr={stderr}")
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError("invalid daemon payload")
        return payload

    def send(self, cmd: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if self._proc.stdin is None:
            raise RuntimeError("daemon stdin unavailable")
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "id": request_id,
            "cmd": cmd,
            "params": params or {},
        }
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        response = self._read_response()
        if response.get("id") != request_id:
            raise RuntimeError(f"daemon response id mismatch: expected {request_id}, got {response.get('id')}")
        if not response.get("ok"):
            raise RuntimeError(f"daemon command '{cmd}' failed: {response.get('error')}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"daemon command '{cmd}' returned invalid result payload")
        return result

    def close(self) -> None:
        try:
            self.send("shutdown")
        except Exception:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect NYC second-degree insurance ICP LinkedIn people search results into CSV."
    )
    parser.add_argument("--session-state", type=Path, default=DEFAULT_SESSION_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--max-pages-per-query", type=int, default=DEFAULT_MAX_PAGES_PER_QUERY)
    parser.add_argument("--daemon-script", type=Path, default=DEFAULT_DAEMON_SCRIPT)
    parser.add_argument("--headed", action="store_true", help="Run headed daemon browser (requires X server).")
    return parser.parse_args()


def _collect_profiles(
    *,
    client: DaemonClient,
    target_count: int,
    max_pages_per_query: int,
) -> tuple[list[dict[str, str | int]], int]:
    seen_urls: set[str] = set()
    rows: list[dict[str, str | int]] = []
    pages_visited = 0

    auth = client.send("assert_authenticated")
    if not bool(auth.get("authenticated")):
        raise RuntimeError(
            "LinkedIn session state is not authenticated (redirected to login). "
            "Refresh .build/enrichment/sessions/linkedin.com/storage-state.json and retry."
        )

    for query in SEARCH_QUERIES:
        if len(rows) >= target_count:
            break
        composed_query = f"{query} New York City Metropolitan Area"
        client.send("open_people_search", {"query": composed_query})

        for _ in range(max_pages_per_query):
            pages_visited += 1
            payload = client.send("scrape_people_cards")
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
            client.send("sleep", {"seconds": sleep_seconds})
            moved = client.send("next_page")
            if not bool(moved.get("moved")):
                break

    return rows, pages_visited


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


def main() -> int:
    args = _parse_args()
    if not args.session_state.exists():
        raise SystemExit(f"session state file not found: {args.session_state}")
    if not args.daemon_script.exists():
        raise SystemExit(f"daemon script not found: {args.daemon_script}")
    if args.target_count <= 0:
        raise SystemExit("target-count must be > 0")
    if args.max_pages_per_query <= 0:
        raise SystemExit("max-pages-per-query must be > 0")

    client = DaemonClient(
        daemon_script=args.daemon_script,
        session_state=args.session_state,
        headed=args.headed,
    )
    try:
        rows, pages_visited = _collect_profiles(
            client=client,
            target_count=args.target_count,
            max_pages_per_query=args.max_pages_per_query,
        )
    except RuntimeError as exc:
        print(f"error={exc}")
        return 2
    finally:
        client.close()

    output_path = args.output.resolve()
    _write_csv(output_path, rows)
    print(f"output_csv={output_path}")
    print(f"profiles_collected={len(rows)}")
    print(f"pages_visited={pages_visited}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
