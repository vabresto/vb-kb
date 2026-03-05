from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class LinkedInDaemonClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 20.0) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url must be non-empty")
        self.base_url = normalized
        self.timeout_seconds = timeout_seconds

    def _url(self, path: str) -> str:
        return urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {"accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        request = urllib.request.Request(
            self._url(path),
            method=method,
            data=data,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = ""
            try:
                details = exc.read().decode("utf-8")
            except Exception:
                details = str(exc)
            raise RuntimeError(f"daemon request failed ({method} {path}): HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"daemon request failed ({method} {path}): {exc.reason}") from exc
        if not body.strip():
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"daemon returned non-object JSON for {method} {path}")
        return parsed

    def health(self) -> dict[str, Any]:
        return self._request_json(method="GET", path="/health")

    def state(self) -> dict[str, Any]:
        return self._request_json(method="GET", path="/api/state")

    def set_mode(self, *, mode: str, actor: str = "agent-client", reason: str = "") -> dict[str, Any]:
        return self._request_json(
            method="POST",
            path="/api/mode",
            payload={"mode": mode, "actor": actor, "reason": reason},
        )

    def command(self, *, cmd: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request_json(
            method="POST",
            path="/api/command",
            payload={"cmd": cmd, "params": params or {}},
        )
        if not bool(response.get("ok")):
            raise RuntimeError(str(response.get("error") or f"command '{cmd}' failed"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"invalid daemon result payload for command '{cmd}'")
        return result

    def shutdown(self) -> dict[str, Any]:
        return self._request_json(method="POST", path="/api/shutdown", payload={})

    def wait_until_ready(self, *, timeout_seconds: float = 20.0, interval_seconds: float = 0.5) -> None:
        deadline = time.time() + timeout_seconds
        while True:
            try:
                response = self.health()
                if bool(response.get("ok")):
                    return
            except Exception:
                pass
            if time.time() >= deadline:
                break
            time.sleep(interval_seconds)
        raise RuntimeError(f"daemon at {self.base_url} did not become ready within {timeout_seconds:.1f}s")

