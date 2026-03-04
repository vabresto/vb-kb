#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

EXTRACT_CARD_DATA_JS = r"""
() => {
  let rows = Array.from(document.querySelectorAll("li.reusable-search__result-container"));
  if (rows.length === 0) {
    rows = Array.from(document.querySelectorAll("div.entity-result"));
  }
  const out = [];
  for (const row of rows) {
    const profileAnchor = row.querySelector("a[href*='/in/']");
    if (!profileAnchor) continue;

    const href = profileAnchor.href || profileAnchor.getAttribute("href") || "";
    const name = (profileAnchor.textContent || "").trim().replace(/\s+/g, " ");
    const textRaw = (row.innerText || "").replace(/\u00a0/g, " ");
    const lines = textRaw
      .split("\n")
      .map((line) => line.trim().replace(/\s+/g, " "))
      .filter(Boolean);
    const allText = lines.join(" | ");

    let degree = "";
    for (const line of lines) {
      const m = line.match(/\b([123](?:st|nd|rd))\b/i);
      if (m) {
        degree = m[1];
        break;
      }
    }

    const subtitleEl = row.querySelector(".entity-result__primary-subtitle");
    const secondaryEl = row.querySelector(".entity-result__secondary-subtitle");
    let subtitle = (subtitleEl?.textContent || "").trim().replace(/\s+/g, " ");
    let location = (secondaryEl?.textContent || "").trim().replace(/\s+/g, " ");
    let mutualLine = "";

    for (const line of lines) {
      if (!mutualLine && /mutual connection/i.test(line)) {
        mutualLine = line;
      }
      if (!subtitle) {
        if (/^(1st|2nd|3rd)$/i.test(line)) continue;
        if (/mutual connection/i.test(line)) continue;
        if (/^(follow|connect|message|pending)$/i.test(line)) continue;
        if (name && line === name) continue;
        subtitle = line;
      }
      if (!location) {
        if (
          /metropolitan area|new york|brooklyn|queens|manhattan|bronx|staten island|jersey city|hoboken|long island city/i.test(line)
        ) {
          location = line;
        }
      }
    }

    out.push({
      href,
      name,
      degree,
      subtitle,
      location,
      mutual_line: mutualLine,
      all_text: allText,
    });
  }
  return out;
}
"""


class LinkedInPlaywrightDaemon:
    def __init__(self, *, session_state_path: Path, headless: bool) -> None:
        self._session_state_path = session_state_path
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def start(self) -> None:
        if not self._session_state_path.exists():
            raise RuntimeError(f"session state file not found: {self._session_state_path}")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            storage_state=str(self._session_state_path.resolve()),
            viewport={"width": 1440, "height": 980},
        )
        self._page = self._context.new_page()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    @property
    def page(self):  # noqa: ANN201
        if self._page is None:
            raise RuntimeError("daemon is not started")
        return self._page

    def _wait_idle(self, *, timeout_ms: int = 10_000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    @staticmethod
    def _is_login_redirect(url: str) -> bool:
        lowered = url.lower()
        return "linkedin.com/uas/login" in lowered or "/login" in lowered

    def handle(self, cmd: str, params: dict[str, object]) -> dict[str, object]:
        if cmd == "ping":
            return {"message": "pong"}

        if cmd == "assert_authenticated":
            self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=90_000)
            self._wait_idle(timeout_ms=12_000)
            self.page.wait_for_timeout(1_200)
            current = self.page.url
            return {
                "authenticated": not self._is_login_redirect(current),
                "url": current,
                "title": self.page.title(),
            }

        if cmd == "open_people_search":
            query = str(params.get("query") or "").strip()
            if not query:
                raise RuntimeError("open_people_search requires non-empty query")
            url = (
                "https://www.linkedin.com/search/results/people/"
                f"?keywords={quote_plus(query)}&network=%5B%22S%22%5D"
            )
            self.page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            self._wait_idle(timeout_ms=12_000)
            self.page.wait_for_timeout(1_400)
            return {
                "url": self.page.url,
                "title": self.page.title(),
                "results_visible": self.page.locator("li.reusable-search__result-container, div.entity-result").count(),
            }

        if cmd == "scrape_people_cards":
            for _ in range(2):
                self.page.mouse.wheel(0, 1400)
                self.page.wait_for_timeout(280)
            self.page.wait_for_timeout(700)
            cards = self.page.evaluate(EXTRACT_CARD_DATA_JS)
            return {"cards": cards}

        if cmd == "next_page":
            selectors = (
                "button[aria-label='Next']",
                "button.artdeco-pagination__button--next",
                "button:has-text('Next')",
            )
            for selector in selectors:
                locator = self.page.locator(selector)
                if locator.count() == 0:
                    continue
                button = locator.first
                disabled = button.get_attribute("disabled")
                aria_disabled = str(button.get_attribute("aria-disabled") or "").strip().lower()
                classes = str(button.get_attribute("class") or "").strip().lower()
                if disabled is not None or aria_disabled == "true" or "disabled" in classes:
                    return {"moved": False}
                button.click()
                self._wait_idle(timeout_ms=10_000)
                self.page.wait_for_timeout(1_200)
                return {"moved": True, "url": self.page.url, "title": self.page.title()}
            return {"moved": False}

        if cmd == "sleep":
            seconds = float(params.get("seconds") or 0)
            if seconds > 0:
                time.sleep(seconds)
            return {"slept_seconds": seconds}

        if cmd == "current_page":
            return {"url": self.page.url, "title": self.page.title()}

        if cmd == "shutdown":
            return {"closing": True}

        raise RuntimeError(f"unknown command: {cmd}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-running LinkedIn Playwright daemon over stdin/stdout JSON.")
    parser.add_argument(
        "--session-state",
        type=Path,
        default=Path(".build/enrichment/sessions/linkedin.com/storage-state.json"),
        help="Path to Playwright storage-state JSON.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch headed browser (requires X server).",
    )
    return parser.parse_args()


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    args = _parse_args()
    daemon = LinkedInPlaywrightDaemon(
        session_state_path=args.session_state,
        headless=not args.headed,
    )

    try:
        daemon.start()
    except Exception as exc:
        _emit({"ok": False, "error": str(exc), "event": "startup_failed"})
        return 1

    _emit({"ok": True, "event": "ready"})

    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            request_id: object = None
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("request must be JSON object")
                request_id = payload.get("id")
                cmd = str(payload.get("cmd") or "").strip()
                params_raw = payload.get("params", {})
                params = params_raw if isinstance(params_raw, dict) else {}
                result = daemon.handle(cmd, params)
                _emit({"id": request_id, "ok": True, "result": result})
                if cmd == "shutdown":
                    break
            except Exception as exc:
                _emit({"id": request_id, "ok": False, "error": str(exc)})
    finally:
        daemon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

