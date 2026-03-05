#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
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
  const clean = (value) => String(value || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  const cleanUrl = (href) => {
    const raw = String(href || "").trim();
    if (!raw) return "";
    try {
      const u = new URL(raw, location.origin);
      if (!/linkedin\.com$/i.test(u.hostname) && !/\.linkedin\.com$/i.test(u.hostname)) return "";
      if (!/\/in\//i.test(u.pathname)) return "";
      return `${u.origin}${u.pathname}`;
    } catch {
      return "";
    }
  };

  const findProfileAnchor = (row) => {
    const anchors = Array.from(row.querySelectorAll("a[href*='/in/']")).filter((a) => {
      const href = a.getAttribute("href") || "";
      return /\/in\//i.test(href) && !/\/linkedin\/learning\//i.test(href) && !/\/pulse\//i.test(href);
    });
    if (!anchors.length) return null;
    for (const anchor of anchors) {
      const hidden = clean(anchor.querySelector(".visually-hidden")?.textContent || "");
      const text = clean(anchor.textContent || "");
      if (/^view\s+.+profile$/i.test(hidden) || /^view\s+.+profile$/i.test(text)) {
        return anchor;
      }
    }
    for (const anchor of anchors) {
      const text = clean(anchor.textContent || "").toLowerCase();
      if (!text) continue;
      if (/^(connect|message|follow|ignore|accept|pending|more)\b/.test(text)) continue;
      if (/^status is\b/.test(text)) continue;
      return anchor;
    }
    return anchors[0];
  };

  const parseName = (anchor) => {
    const hidden = clean(anchor.querySelector(".visually-hidden")?.textContent || "");
    const text = clean(anchor.textContent || "");
    const hiddenMatch = /view\s+(.+?)[’'\u2018\u2019]?\s*s?\s*profile/i.exec(hidden);
    if (hiddenMatch) return clean(hiddenMatch[1]);
    const textMatch = /view\s+(.+?)[’'\u2018\u2019]?\s*s?\s*profile/i.exec(text);
    if (textMatch) return clean(textMatch[1]);
    return clean(text.replace(/\s*view\s+.+$/i, ""));
  };

  const collectRows = () => {
    const bySelector = [
      ...document.querySelectorAll("[data-view-name='search-entity-result-universal-template']"),
      ...document.querySelectorAll("li.reusable-search__result-container"),
      ...document.querySelectorAll("div.entity-result"),
    ];
    if (bySelector.length > 0) return bySelector;
    return Array.from(document.querySelectorAll("li")).filter((li) => li.querySelector("a[href*='/in/']"));
  };

  const rows = collectRows();
  const out = [];
  const seen = new Set();
  for (const row of rows) {
    const profileAnchor = findProfileAnchor(row);
    if (!profileAnchor) continue;
    const href = cleanUrl(profileAnchor.getAttribute("href") || profileAnchor.href || "");
    if (!href || seen.has(href)) continue;
    seen.add(href);

    const name = parseName(profileAnchor);
    const textRaw = String(row.innerText || row.textContent || "").replace(/\u00a0/g, " ");
    const lines = textRaw
      .split(/\n+/)
      .map((line) => clean(line))
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

    let subtitle = clean(row.querySelector(".entity-result__primary-subtitle")?.textContent || "");
    let location = clean(row.querySelector(".entity-result__secondary-subtitle")?.textContent || "");
    let mutualLine = "";
    for (const line of lines) {
      if (!mutualLine && /mutual connection/i.test(line)) {
        mutualLine = line;
      }
      if (!subtitle) {
        if (name && line === name) continue;
        if (/^(1st|2nd|3rd)$/i.test(line)) continue;
        if (/mutual connection/i.test(line)) continue;
        if (/^(follow|connect|message|pending|more)$/i.test(line)) continue;
        subtitle = line;
      }
      if (!location && /metropolitan area|new york|brooklyn|queens|manhattan|bronx|staten island|jersey city|hoboken|long island city/i.test(line)) {
        location = line;
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
    input[type='text'] { background:#0b1220; color:var(--fg); border:1px solid #374151; border-radius:8px; padding:8px 10px; min-width: 360px; }
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
    <div class="panel">
      <h3>Session State</h3>
      <div class="row">
        <input id="sessionPathInput" type="text" />
        <button id="btnSaveState">Save Session State JSON</button>
      </div>
      <div id="sessionInfo" class="mono"></div>
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
      if (!response.ok) throw new Error(await response.text());
      return await response.json();
    }

    async function saveSessionState(path) {
      const response = await fetch('/api/save-session-state', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!response.ok) throw new Error(await response.text());
      return await response.json();
    }

    function render(state) {
      const modeLine = document.getElementById('modeLine');
      const updatedLine = document.getElementById('updatedLine');
      const pageUrl = document.getElementById('pageUrl');
      const pageTitle = document.getElementById('pageTitle');
      const authState = document.getElementById('authState');
      const sessionPathInput = document.getElementById('sessionPathInput');
      const sessionInfo = document.getElementById('sessionInfo');

      const mode = state.mode || 'unknown';
      modeLine.textContent = `Mode: ${mode}`;
      modeLine.className = `mode ${mode === 'autonomous' ? 'auto' : 'human'}`;
      updatedLine.textContent = `updated_at=${state.updated_at || ''} by=${state.updated_by || ''} reason=${state.reason || ''}`;
      pageUrl.textContent = `url=${state.automation_url || ''}`;
      pageTitle.textContent = `title=${state.automation_title || ''}`;
      authState.textContent = `authenticated=${state.authenticated} | control_page_open=${state.control_page_open}`;
      if (!sessionPathInput.value) {
        sessionPathInput.value = state.session_state_path || '';
      }
      sessionInfo.textContent = `configured_path=${state.session_state_path || ''} | last_saved_at=${state.last_saved_at || ''}`;
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
    document.getElementById('btnSaveState').addEventListener('click', async () => {
      const input = document.getElementById('sessionPathInput');
      const chosenPath = (input.value || '').trim();
      try {
        const payload = await saveSessionState(chosenPath);
        document.getElementById('actionStatus').textContent = `Saved session state to ${payload.saved_path}`;
        await refresh();
      } catch (err) {
        document.getElementById('actionStatus').textContent = `save failed: ${String(err)}`;
      }
    });
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


@dataclass
class _WorkerTask:
    cmd: str
    params: dict[str, object]
    response_queue: queue.Queue[tuple[bool, object]]


class LinkedInPlaywrightWorker:
    def __init__(self, *, session_state_path: Path, headless: bool, open_control_tab: bool) -> None:
        self._session_state_path = session_state_path
        self._headless = headless
        self._open_control_tab = open_control_tab
        self._control_url: str | None = None
        self._thread: threading.Thread | None = None
        self._tasks: queue.Queue[_WorkerTask] = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error: str | None = None
        self._next_control_check = 0.0

    @staticmethod
    def _is_login_redirect(url: str) -> bool:
        lowered = url.lower()
        return "linkedin.com/uas/login" in lowered or "/login" in lowered

    def start(self) -> None:
        if not self._session_state_path.exists():
            raise RuntimeError(f"session state file not found: {self._session_state_path}")
        self._thread = threading.Thread(target=self._run, name="linkedin-playwright-worker", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=20):
            raise RuntimeError("playwright worker did not become ready")
        if self._startup_error is not None:
            raise RuntimeError(self._startup_error)

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            self.call(cmd="shutdown_worker")
        except Exception:
            pass
        self._stop.set()
        self._thread.join(timeout=10)

    def set_control_url(self, control_url: str) -> None:
        self.call(cmd="set_control_url", params={"control_url": control_url})

    def call(self, *, cmd: str, params: dict[str, object] | None = None) -> dict[str, object]:
        response_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
        self._tasks.put(_WorkerTask(cmd=cmd, params=params or {}, response_queue=response_queue))
        try:
            ok, payload = response_queue.get(timeout=120)
        except queue.Empty as exc:
            raise RuntimeError(f"playwright worker timed out while handling '{cmd}'") from exc
        if not ok:
            raise RuntimeError(str(payload))
        if not isinstance(payload, dict):
            raise RuntimeError(f"invalid worker response for '{cmd}'")
        return payload

    def _run(self) -> None:
        playwright = None
        browser = None
        automation_context = None
        automation_page = None
        control_context = None
        control_page = None

        def wait_idle(page: Any, timeout_ms: int = 10_000) -> None:
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass

        def ensure_control_page() -> None:
            nonlocal control_page
            if not self._open_control_tab:
                return
            if not self._control_url:
                return
            if control_context is None:
                return
            if control_page is not None and not control_page.is_closed():
                return
            page = control_context.new_page()
            page.goto(self._control_url, wait_until="domcontentloaded", timeout=30_000)
            control_page = page

        try:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=self._headless)
            automation_context = browser.new_context(
                storage_state=str(self._session_state_path.resolve()),
                viewport={"width": 1440, "height": 980},
            )
            automation_page = automation_context.new_page()
            control_context = browser.new_context(viewport={"width": 1200, "height": 900})
            self._ready.set()

            while not self._stop.is_set():
                now = time.monotonic()
                if now >= self._next_control_check:
                    self._next_control_check = now + 2.0
                    try:
                        ensure_control_page()
                    except Exception:
                        pass

                try:
                    task = self._tasks.get(timeout=0.2)
                except queue.Empty:
                    continue

                try:
                    cmd = task.cmd.strip()
                    params = task.params

                    if cmd == "shutdown_worker":
                        task.response_queue.put((True, {"closing": True}))
                        break

                    if cmd == "set_control_url":
                        self._control_url = str(params.get("control_url") or "")
                        task.response_queue.put((True, {"ok": True}))
                        continue

                    if cmd == "state_snapshot":
                        automation_url = ""
                        automation_title = ""
                        authenticated: bool | None = None
                        if automation_page is not None and not automation_page.is_closed():
                            try:
                                automation_url = automation_page.url
                                automation_title = automation_page.title()
                                if "linkedin.com" in automation_url.lower():
                                    authenticated = not self._is_login_redirect(automation_url)
                                else:
                                    authenticated = None
                            except Exception:
                                authenticated = None
                        payload = {
                            "automation_url": automation_url,
                            "automation_title": automation_title,
                            "authenticated": authenticated,
                            "control_page_open": bool(control_page is not None and not control_page.is_closed()),
                            "session_state_path": str(self._session_state_path.resolve()),
                        }
                        task.response_queue.put((True, payload))
                        continue

                    if cmd == "assert_authenticated":
                        automation_page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=90_000)
                        wait_idle(automation_page, timeout_ms=12_000)
                        automation_page.wait_for_timeout(1_200)
                        current = automation_page.url
                        task.response_queue.put(
                            (
                                True,
                                {
                                    "authenticated": not self._is_login_redirect(current),
                                    "url": current,
                                    "title": automation_page.title(),
                                },
                            )
                        )
                        continue

                    if cmd == "open_people_search":
                        query = str(params.get("query") or "").strip()
                        if not query:
                            raise RuntimeError("open_people_search requires non-empty query")
                        url = (
                            "https://www.linkedin.com/search/results/people/"
                            f"?keywords={quote_plus(query)}&facetNetwork=%5B%22S%22%5D&origin=GLOBAL_SEARCH_HEADER"
                        )
                        automation_page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                        wait_idle(automation_page, timeout_ms=12_000)
                        automation_page.wait_for_timeout(1_400)
                        task.response_queue.put(
                            (
                                True,
                                {
                                    "url": automation_page.url,
                                    "title": automation_page.title(),
                                    "results_visible": automation_page.locator(
                                        "[data-view-name='search-entity-result-universal-template'], "
                                        "li.reusable-search__result-container, div.entity-result"
                                    ).count(),
                                },
                            )
                        )
                        continue

                    if cmd == "scrape_people_cards":
                        for _ in range(2):
                            automation_page.mouse.wheel(0, 1400)
                            automation_page.wait_for_timeout(280)
                        automation_page.wait_for_timeout(700)
                        cards = automation_page.evaluate(EXTRACT_CARD_DATA_JS)
                        task.response_queue.put((True, {"cards": cards}))
                        continue

                    if cmd == "next_page":
                        selectors = (
                            "button[aria-label='Next']",
                            "button.artdeco-pagination__button--next",
                            "button:has-text('Next')",
                        )
                        moved = False
                        for selector in selectors:
                            locator = automation_page.locator(selector)
                            if locator.count() == 0:
                                continue
                            button = locator.first
                            disabled = button.get_attribute("disabled")
                            aria_disabled = str(button.get_attribute("aria-disabled") or "").strip().lower()
                            classes = str(button.get_attribute("class") or "").strip().lower()
                            if disabled is not None or aria_disabled == "true" or "disabled" in classes:
                                break
                            button.click()
                            wait_idle(automation_page, timeout_ms=10_000)
                            automation_page.wait_for_timeout(1_200)
                            moved = True
                            break
                        task.response_queue.put(
                            (
                                True,
                                {
                                    "moved": moved,
                                    "url": automation_page.url,
                                    "title": automation_page.title(),
                                },
                            )
                        )
                        continue

                    if cmd == "sleep":
                        seconds = float(params.get("seconds") or 0)
                        if seconds > 0:
                            time.sleep(seconds)
                        task.response_queue.put((True, {"slept_seconds": seconds}))
                        continue

                    if cmd == "current_page":
                        task.response_queue.put(
                            (True, {"url": automation_page.url, "title": automation_page.title()})
                        )
                        continue

                    if cmd == "save_session_state":
                        raw_path = str(params.get("path") or "").strip()
                        destination = Path(raw_path) if raw_path else self._session_state_path
                        destination = destination.expanduser().resolve()
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        automation_context.storage_state(path=str(destination))
                        task.response_queue.put(
                            (
                                True,
                                {
                                    "saved_path": str(destination),
                                    "saved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                                },
                            )
                        )
                        continue

                    raise RuntimeError(f"unknown command: {cmd}")
                except Exception as exc:  # noqa: PERF203
                    task.response_queue.put((False, str(exc)))
        except Exception as exc:
            self._startup_error = str(exc)
            self._ready.set()
        finally:
            if automation_context is not None:
                try:
                    automation_context.close()
                except Exception:
                    pass
            if control_context is not None:
                try:
                    control_context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass


class LinkedInPlaywrightDaemon:
    def __init__(
        self,
        *,
        session_state_path: Path,
        state_path: Path,
        headless: bool,
        open_control_tab: bool,
    ) -> None:
        self._state_path = state_path
        self._state_lock = threading.RLock()
        self._state = load_state(state_path)
        if not isinstance(self._state, dict):
            self._state = {"mode": DAEMON_MODE_AUTONOMOUS}
        persist_state(self._state_path, self._state)
        self._shutdown_requested = threading.Event()
        self._worker = LinkedInPlaywrightWorker(
            session_state_path=session_state_path,
            headless=headless,
            open_control_tab=open_control_tab,
        )
        self._last_saved_at: str | None = None

    def start(self) -> None:
        self._worker.start()

    def close(self) -> None:
        self._worker.stop()

    def set_control_url(self, control_url: str) -> None:
        self._worker.set_control_url(control_url)

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
        runtime = self._worker.call(cmd="state_snapshot")
        base.update(runtime)
        base["shutdown_requested"] = self._shutdown_requested.is_set()
        base["last_saved_at"] = self._last_saved_at
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
        if cmd == "shutdown":
            self.request_shutdown()
            return {"closing": True}
        if cmd == "save_session_state":
            result = self._worker.call(cmd=cmd, params=params)
            self._last_saved_at = str(result.get("saved_at") or "")
            return result

        mode = self.mode()
        if not command_allowed_in_mode(mode=mode, cmd=cmd):
            raise RuntimeError("daemon is in human_control mode; command is blocked until autonomous mode resumes")
        return self._worker.call(cmd=cmd, params=params)

    def save_session_state(self, *, path: str | None) -> dict[str, object]:
        result = self._worker.call(cmd="save_session_state", params={"path": path or ""})
        self._last_saved_at = str(result.get("saved_at") or "")
        return result


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

            if path == "/api/save-session-state":
                try:
                    selected = str(payload.get("path") or "")
                    result = daemon.save_session_state(path=selected)
                    _write_json(self, HTTPStatus.OK, {"ok": True, **result})
                except Exception as exc:
                    _write_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
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
        print(json.dumps({"ok": False, "event": "startup_failed", "error": str(exc)}), flush=True)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), _build_handler(daemon, lambda: server.shutdown()))
    control_url = f"http://{args.host}:{args.port}/control"
    daemon.set_control_url(control_url)

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
        ),
        flush=True,
    )

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        daemon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
