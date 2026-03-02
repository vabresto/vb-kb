from __future__ import annotations

from kb.enrichment_runtime_logging import runtime_log, runtime_logs_enabled


def test_runtime_logs_enabled_defaults_true() -> None:
    assert runtime_logs_enabled({}) is True


def test_runtime_logs_enabled_respects_env_override() -> None:
    assert runtime_logs_enabled({"KB_ENRICHMENT_RUNTIME_LOGS": "false"}) is False
    assert runtime_logs_enabled({"KB_ENRICHMENT_RUNTIME_LOGS": "true"}) is True


def test_runtime_log_is_silent_when_disabled(capsys) -> None:
    runtime_log(
        "test-component",
        "should not print",
        environ={"KB_ENRICHMENT_RUNTIME_LOGS": "false"},
    )
    captured = capsys.readouterr()
    assert captured.err == ""
