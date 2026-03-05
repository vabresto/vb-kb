#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from kb.linkedin_daemon import (
    DAEMON_MODE_AUTONOMOUS,
    build_mode_state,
    command_allowed_in_mode,
    load_state,
    persist_state,
)
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

CONTROL_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LinkedIn Daemon Control</title>
  <style>
    :root { --bg:#0f172a; --fg:#e2e8f0; --muted:#94a3b8; --ok:#22c55e; --warn:#f59e0b; --panel:#111827; }
    body { margin:0; font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--fg); }
    .wrap { max-width: 920px; margin: 24px auto; padding: 0 16px; }
    .panel { background: var(--panel); border: 1px solid #1f2937; border-radius: 12px; padding: 16px; margin-bottom: 12px; }
    .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
    button { background:#1f2937; color:var(--fg); border:1px solid #374151; border-radius:8px; padding:8px 12px; cursor:pointer; }
    button:hover { background:#374151; }
    .mode { font-weight:700; }
    .auto { color: var(--ok); }
    .human { color: var(--warn); }
    .mono { font-family: ui-monospace,SFMono-Regular,Menlo,monospace; font-size: 13px; color: var(--muted); }
    .status { margin-top: 8px; color: var(--muted); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h2>LinkedIn Daemon Control</h2>
      <div id="modeLine" class="mode">Mode: ...</div>
      <div id="updatedLine" class="mono"></div>
      <div class="row" style="margin-top:10px;">
        <button id="btnHuman">Take Human Control</button>
        <button id="btnAuto">Resume Autonomous</button>
        <button id="btnRefresh">Refresh</button>
      </div>
      <div id="actionStatus" class="status"></div>
    </div>
    <div class="panel">
      <h3>Automation Page</h3>
      <div id="pageUrl" class="mono"></div>
      <div id="pageTitle" class="mono"></div>
      <div id="authState" class="mono"></div>
    </div>
  </div>
  <script>
    async function getState() {
      const response = await fetch('/api/state');
      if (!response.ok) throw new Error('state request failed');
      return await response.json();
    }

    async function setMode(mode) {
      const response = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ mode, actor: 'human-ui', reason: 'manual-toggle' }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || 'mode update failed');
      }
      return await response.json();
    }

    function render(state) {
      const modeLine = document.getElementById('modeLine');
      const updatedLine = document.getElementById('updatedLine');
      const pageUrl = document.getElementById('pageUrl');
      const pageTitle = document.getElementById('pageTitle');
      const authState = document.getElementById('authState');

      const mode = state.mode || 'unknown';
      modeLine.textContent = `Mode: ${mode}`;
      modeLine.className = `mode ${mode === 'autonomous' ? 'auto' : 'human'}`;
      updatedLine.textContent = `updated_at=${state.updated_at || ''} by=${state.updated_by || ''} reason=${state.reason || ''}`;
      pageUrl.textContent = `url=${state.automation_url || ''}`;
      pageTitle.textContent = `title=${state.automation_title || ''}`;
      authState.textContent = `authenticated=${state.authenticated} | control_page_open=${state.control_page_open}`;
    }

    async function refresh() {
      try {
        const state = await getState();
        render(state);
      } catch (err) {
        document.getElementById('actionStatus').textContent = `refresh failed: ${String(err)}`;
      }
    }

    document.getElementById('btnHuman').addEventListener('click', async () => {
      try {
        const state = await setMode('human_control');
        render(state);
        document.getElementById('actionStatus').textContent = 'Switched to human_control';
      } catch (err) {
        document.getElementById('actionStatus').textContent = `mode switch failed: ${String(err)}`;
      }
    });

    document.getElementById('btnAuto').addEventListener('click', async () => {
      try {
        const state = await setMode('autonomous');
        render(state);
        document.getElementById('actionStatus').textContent = 'Switched to autonomous';
      } catch (err) {
        document.getElementById('actionStatus').textContent = `mode switch failed: ${String(err)}`;
      }
    });

    document.getElementById('btnRefresh').addEventListener('click', refresh);
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


class LinkedInPlaywrightDaemon:
    def __init__(
        self,
        *,
        session_state_path: Path,
        state_path: Path,
        headless: bool,
        open_control_tab: bool,
    ) -> None:
        self._session_state_path = session_state_path
        self._state_path = state_path
        self._headless = headless
        self._open_control_tab = open_control_tab
        self._playwright = None
        self._browser = None
        self._automation_context = None
        self._automation_page = None
        self._control_context = None
        self._control_page = None
        self._playwright_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._state = load_state(state_path)
        if not isinstance(self._state, dict):
            self._state = {"mode": DAEMON_MODE_AUTONOMOUS}
        persist_state(self._state_path, self._state)
        self._control_url: str | None = None
        self._shutdown_requested = threading.Event()

    def set_control_url(self, control_url: str) -> None:
        self._control_url = control_url

    def start(self) -> None:
        if not self._session_state_path.exists():
            raise RuntimeError(f"session state file not found: {self._session_state_path}")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._automation_context = self._browser.new_context(
            storage_state=str(self._session_state_path.resolve()),
            viewport={"width": 1440, "height": 980},
        )
        self._automation_page = self._automation_context.new_page()
        self._control_context = self._browser.new_context(viewport={"width": 1200, "height": 900})

    def close(self) -> None:
        with self._playwright_lock:
            if self._automation_context is not None:
                self._automation_context.close()
                self._automation_context = None
            if self._control_context is not None:
                self._control_context.close()
                self._control_context = None
            if self._browser is not None:
                self._browser.close()
                self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    @property
    def page(self):  # noqa: ANN201
        if self._automation_page is None:
            raise RuntimeError("automation page is not available")
        return self._automation_page

    @staticmethod
    def _is_login_redirect(url: str) -> bool:
        lowered = url.lower()
        return "linkedin.com/uas/login" in lowered or "/login" in lowered

    def _wait_idle(self, *, timeout_ms: int = 10_000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    def ensure_control_page(self) -> None:
        if not self._open_control_tab:
            return
        if not self._control_url:
            return
        with self._playwright_lock:
            if self._control_context is None:
                return
            if self._control_page is not None and not self._control_page.is_closed():
                return
            page = self._control_context.new_page()
            page.goto(self._control_url, wait_until="domcontentloaded", timeout=30_000)
            self._control_page = page

    def mode(self) -> str:
        with self._state_lock:
            return str(self._state.get("mode") or DAEMON_MODE_AUTONOMOUS)

    def set_mode(self, *, mode: str, actor: str, reason: str) -> dict[str, Any]:
        with self._state_lock:
            self._state = build_mode_state(mode=mode, actor=actor, reason=reason)
            persist_state(self._state_path, self._state)
        return self.state_snapshot()

    def state_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            base = dict(self._state)
        with self._playwright_lock:
            automation_url = ""
            automation_title = ""
            authenticated: bool | None = None
            try:
                if self._automation_page is not None and not self._automation_page.is_closed():
                    automation_url = self._automation_page.url
                    automation_title = self._automation_page.title()
                    authenticated = not self._is_login_redirect(automation_url)
            except Exception:
                authenticated = None
            control_page_open = bool(self._control_page is not None and not self._control_page.is_closed())
        base.update(
            {
                "automation_url": automation_url,
                "automation_title": automation_title,
                "authenticated": authenticated,
                "control_page_open": control_page_open,
                "shutdown_requested": self._shutdown_requested.is_set(),
            }
        )
        return base

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def shutdown_requested(self) -> bool:
        return self._shutdown_requested.is_set()

    def handle_command(self, cmd: str, params: dict[str, object]) -> dict[str, object]:
        cmd = cmd.strip()
        if not cmd:
            raise RuntimeError("cmd must be non-empty")

        if cmd == "ping":
            return {"message": "pong"}
        if cmd == "get_state":
            return self.state_snapshot()

        mode = self.mode()
        if not command_allowed_in_mode(mode=mode, cmd=cmd):
            raise RuntimeError("daemon is in human_control mode; command is blocked until autonomous mode resumes")

        with self._playwright_lock:
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
                    "results_visible": self.page.locator(
                        "li.reusable-search__result-container, div.entity-result"
                    ).count(),
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
                self.request_shutdown()
                return {"closing": True}

        raise RuntimeError(f"unknown command: {cmd}")


def _parse_json_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    content_len = int(handler.headers.get("content-length", "0") or "0")
    if content_len <= 0:
        return {}
    raw = handler.rfile.read(content_len)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_html(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "text/html; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_handler(daemon: LinkedInPlaywrightDaemon, shutdown_server: callable):  # noqa: ANN001
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                _write_json(self, HTTPStatus.OK, {"ok": True})
                return
            if path == "/api/state":
                _write_json(self, HTTPStatus.OK, daemon.state_snapshot())
                return
            if path == "/control":
                _write_html(self, HTTPStatus.OK, CONTROL_HTML)
                return
            _write_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = _parse_json_body(self)
            except Exception as exc:
                _write_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            if path == "/api/mode":
                try:
                    mode = str(payload.get("mode") or "")
                    actor = str(payload.get("actor") or "api-client")
                    reason = str(payload.get("reason") or "")
                    state = daemon.set_mode(mode=mode, actor=actor, reason=reason)
                    _write_json(self, HTTPStatus.OK, state)
                except Exception as exc:
                    _write_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            if path == "/api/command":
                cmd = str(payload.get("cmd") or "")
                params_raw = payload.get("params", {})
                params = params_raw if isinstance(params_raw, dict) else {}
                try:
                    result = daemon.handle_command(cmd, params)
                    _write_json(self, HTTPStatus.OK, {"ok": True, "result": result})
                except Exception as exc:
                    _write_json(self, HTTPStatus.CONFLICT, {"ok": False, "error": str(exc), "cmd": cmd})
                return

            if path == "/api/shutdown":
                daemon.request_shutdown()
                _write_json(self, HTTPStatus.OK, {"ok": True, "message": "shutdown requested"})
                threading.Thread(target=shutdown_server, daemon=True).start()
                return

            _write_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    return Handler


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LinkedIn Playwright daemon server with control UI.")
    parser.add_argument(
        "--session-state",
        type=Path,
        default=Path(".build/enrichment/sessions/linkedin.com/storage-state.json"),
        help="Path to Playwright storage-state JSON.",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path(".build/enrichment/daemon/linkedin-daemon-state.json"),
        help="Persisted daemon mode state path.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Listen host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8771, help="Listen port (default: 8771).")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch headed browser (requires X server, useful for human inspection).",
    )
    parser.add_argument(
        "--no-control-tab",
        action="store_true",
        help="Disable control page auto-open/reopen behavior.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    daemon = LinkedInPlaywrightDaemon(
        session_state_path=args.session_state,
        state_path=args.state_path,
        headless=not args.headed,
        open_control_tab=not args.no_control_tab,
    )
    try:
        daemon.start()
    except Exception as exc:
        print(json.dumps({"ok": False, "event": "startup_failed", "error": str(exc)}))
        return 1

    server = ThreadingHTTPServer((args.host, args.port), _build_handler(daemon, lambda: server.shutdown()))
    control_url = f"http://{args.host}:{args.port}/control"
    daemon.set_control_url(control_url)
    daemon.ensure_control_page()

    stop_monitor = threading.Event()

    def _control_page_monitor() -> None:
        while not stop_monitor.is_set() and not daemon.shutdown_requested():
            try:
                daemon.ensure_control_page()
            except Exception:
                pass
            stop_monitor.wait(2.0)

    monitor_thread = threading.Thread(target=_control_page_monitor, daemon=True)
    monitor_thread.start()

    print(
        json.dumps(
            {
                "ok": True,
                "event": "ready",
                "host": args.host,
                "port": args.port,
                "control_url": control_url,
                "mode": daemon.mode(),
            }
        )
    )

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_monitor.set()
        try:
            server.server_close()
        except Exception:
            pass
        daemon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

