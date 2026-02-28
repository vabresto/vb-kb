#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import http.client
import ipaddress
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
URL_RE = re.compile(r"https?://[^/\s<>(){}\"'`][^\s<>(){}\"'`]*")
USER_AGENT = "vb-kb-url-check/1.0"
SKIPPED_HOSTS = {
    "localhost",
    "example.com",
    "example.net",
    "example.org",
}
SKIPPED_HOST_SUFFIXES = (
    ".localhost",
    ".example",
    ".invalid",
    ".test",
)


@dataclass(frozen=True)
class UrlCheckResult:
    url: str
    ok: bool
    status: int | None
    final_url: str | None
    error: str | None


def _normalize_diff_path(raw: str) -> str | None:
    value = raw.strip()
    if value == "/dev/null":
        return None
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _should_skip_diff_file(path: str | None) -> bool:
    if path is None:
        return False
    pure = pathlib.PurePosixPath(path)
    parts = pure.parts
    return len(parts) >= 5 and parts[0] == "data" and parts[1] == "source" and parts[-1] == "snapshot.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate that newly added URLs in staged diff resolve to final 2xx."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Per-request timeout in seconds (default: 8.0).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Retry count for transient network errors (default: 1).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="Maximum parallel URL checks (default: 8).",
    )
    return parser.parse_args()


def clean_url(raw_url: str) -> str:
    url = raw_url.strip()
    while url and url[-1] in ".,;:!?\"'`>]}":
        url = url[:-1]

    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]

    return url


def should_check_url(url: str) -> bool:
    if "{" in url or "}" in url:
        return False

    try:
        hostname = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False

    if not hostname:
        return False
    hostname = hostname.lower()

    if "{" in hostname or "}" in hostname or "$" in hostname:
        return False

    if hostname in SKIPPED_HOSTS:
        return False
    if any(hostname.endswith(suffix) for suffix in SKIPPED_HOST_SUFFIXES):
        return False

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True

    return not ip.is_loopback


def staged_added_urls() -> list[str]:
    command = ["git", "diff", "--cached", "--unified=0", "--no-color", "--"]
    process = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode not in (0, 1):
        raise RuntimeError(process.stderr.strip() or "git diff --cached failed.")

    urls: set[str] = set()
    current_diff_file: str | None = None
    for line in process.stdout.splitlines():
        if line.startswith("+++ "):
            current_diff_file = _normalize_diff_path(line[4:])
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if _should_skip_diff_file(current_diff_file):
            continue

        for match in URL_RE.findall(line[1:]):
            cleaned = clean_url(match)
            if cleaned and should_check_url(cleaned):
                urls.add(cleaned)

    return sorted(urls)


def fetch_once(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.getcode()
        final_url = response.geturl()
    return status, final_url


def is_transient_http_status(status: int) -> bool:
    if status in {408, 425, 429}:
        return True
    return 500 <= status < 600


def check_url(url: str, timeout: float, retries: int) -> UrlCheckResult:
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            status, final_url = fetch_once(url, timeout)
            if 200 <= status < 300:
                return UrlCheckResult(
                    url=url,
                    ok=True,
                    status=status,
                    final_url=final_url,
                    error=None,
                )

            return UrlCheckResult(
                url=url,
                ok=False,
                status=status,
                final_url=final_url,
                error=f"final status {status} is not 2xx",
            )
        except urllib.error.HTTPError as exc:
            status = exc.code
            final_url = exc.geturl() if hasattr(exc, "geturl") else None
            if attempt < retries and is_transient_http_status(status):
                time.sleep(0.35 * (attempt + 1))
                continue

            return UrlCheckResult(
                url=url,
                ok=False,
                status=status,
                final_url=final_url,
                error=f"HTTP {status}",
            )
        except (
            urllib.error.URLError,
            http.client.InvalidURL,
            TimeoutError,
            ValueError,
        ) as exc:
            if attempt < retries:
                time.sleep(0.35 * (attempt + 1))
                continue

            return UrlCheckResult(
                url=url,
                ok=False,
                status=None,
                final_url=None,
                error=str(exc.reason) if hasattr(exc, "reason") else str(exc),
            )

    return UrlCheckResult(
        url=url,
        ok=False,
        status=None,
        final_url=None,
        error="unknown error",
    )


def run_checks(
    urls: list[str], timeout: float, retries: int, max_workers: int
) -> list[UrlCheckResult]:
    if not urls:
        return []

    results: list[UrlCheckResult] = []
    workers = max(1, min(max_workers, len(urls)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_url, url, timeout, retries): url for url in urls
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    return sorted(results, key=lambda item: item.url)


def main() -> int:
    args = parse_args()

    try:
        urls = staged_added_urls()
    except RuntimeError as exc:
        print(f"URL reachability check failed to read staged diff: {exc}")
        return 1

    if not urls:
        return 0

    results = run_checks(
        urls=urls,
        timeout=args.timeout,
        retries=max(0, args.retries),
        max_workers=max(1, args.max_workers),
    )
    failures = [result for result in results if not result.ok]

    if not failures:
        return 0

    print("Newly added URL reachability check failed:")
    for failure in failures:
        status = f"HTTP {failure.status}" if failure.status is not None else "no response"
        details = failure.error or "request failed"
        if failure.final_url and failure.final_url != failure.url:
            status = f"{status} (final: {failure.final_url})"
        print(f"- {failure.url}: {status} - {details}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
