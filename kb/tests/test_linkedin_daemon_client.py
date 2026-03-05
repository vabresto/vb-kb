from __future__ import annotations

import json
import urllib.error
from unittest import mock

import pytest

from kb.linkedin_daemon_client import LinkedInDaemonClient


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False

    def read(self) -> bytes:
        return self._body


def test_state_request_parses_json_payload() -> None:
    client = LinkedInDaemonClient(base_url="http://127.0.0.1:8771")
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse('{"mode":"autonomous"}')):
        state = client.state()
    assert state["mode"] == "autonomous"


def test_command_returns_result_block() -> None:
    client = LinkedInDaemonClient(base_url="http://127.0.0.1:8771")
    payload = json.dumps({"ok": True, "result": {"authenticated": True}})
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        result = client.command(cmd="assert_authenticated")
    assert result["authenticated"] is True


def test_command_raises_when_non_ok() -> None:
    client = LinkedInDaemonClient(base_url="http://127.0.0.1:8771")
    payload = json.dumps({"ok": False, "error": "blocked"})
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        with pytest.raises(RuntimeError, match="blocked"):
            client.command(cmd="open_people_search", params={"query": "abc"})


def test_http_error_propagates_with_details() -> None:
    client = LinkedInDaemonClient(base_url="http://127.0.0.1:8771")
    body = b'{"ok": false, "error": "nope"}'
    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8771/api/state",
        code=409,
        msg="conflict",
        hdrs=None,
        fp=mock.Mock(read=mock.Mock(return_value=body)),
    )
    with mock.patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(RuntimeError, match="HTTP 409"):
            client.state()

