from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from kb.enrichment_adapters import AntiBotChallengeError, AuthenticationError, MFAChallengeError
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_sessions import export_session_state_json, lookup_session_state, save_session_state
from kb.schemas import KBBaseModel

_MFA_HINTS = ("mfa", "2fa", "two-factor", "verification code", "one-time code")
_ANTI_BOT_HINTS = ("captcha", "anti-bot", "bot challenge", "hcaptcha", "recaptcha")


class BootstrapCommandResult(KBBaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""


BootstrapCommandRunner = Callable[[list[str], Mapping[str, str], Path], BootstrapCommandResult]


class BootstrapSessionResult(KBBaseModel):
    source: SupportedSource
    headless: bool
    bootstrap_command: str
    session_state_path: str
    export_path: str | None = None
    expires_at: datetime | None = None


class BootstrapScriptNotConfiguredError(AuthenticationError):
    def __init__(self, *, source: SupportedSource) -> None:
        env_var = _bootstrap_command_env_var(source)
        super().__init__(
            source=source,
            message="bootstrap command not configured",
            details=(
                "Set the source bootstrap command in enrichment config or via "
                f"'{env_var}', then retry."
            ),
        )


class BootstrapLoginError(AuthenticationError):
    def __init__(
        self,
        *,
        source: SupportedSource,
        step: str,
        reason: str,
        details: str | None = None,
    ) -> None:
        super().__init__(
            source=source,
            message=f"bootstrap failed during '{step}': {reason}",
            details=details,
        )


def bootstrap_session_login(
    source: SupportedSource,
    *,
    config: EnrichmentConfig,
    project_root: Path,
    headless: bool,
    export_path: Path | None = None,
    bootstrap_command: str | None = None,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    command_runner: BootstrapCommandRunner | None = None,
) -> BootstrapSessionResult:
    source_settings = config.sources[source]
    resolved_command = _normalize_optional_token(bootstrap_command) or source_settings.bootstrap_command
    if resolved_command is None:
        raise BootstrapScriptNotConfiguredError(source=source)

    argv = shlex.split(resolved_command)
    if not argv:
        raise BootstrapLoginError(
            source=source,
            step="resolve-command",
            reason="bootstrap command is empty",
            details="Provide a non-empty bootstrap command.",
        )

    resolved_root = project_root.resolve()
    resolved_session_path = resolved_root.joinpath(source_settings.session_state_path)
    resolved_evidence_path = resolved_root.joinpath(source_settings.evidence_path)

    run_env = dict(os.environ if environ is None else environ)
    run_env["KB_ENRICHMENT_BOOTSTRAP_SOURCE"] = source.value
    run_env["KB_ENRICHMENT_BOOTSTRAP_HEADLESS"] = "true" if headless else "false"
    run_env["KB_ENRICHMENT_BOOTSTRAP_SESSION_PATH"] = str(resolved_session_path)
    run_env["KB_ENRICHMENT_BOOTSTRAP_EVIDENCE_PATH"] = str(resolved_evidence_path)

    if source_settings.username_env_var:
        run_env["KB_ENRICHMENT_BOOTSTRAP_USERNAME_ENV"] = source_settings.username_env_var
    if source_settings.password_env_var:
        run_env["KB_ENRICHMENT_BOOTSTRAP_PASSWORD_ENV"] = source_settings.password_env_var
    if source_settings.totp_env_var:
        run_env["KB_ENRICHMENT_BOOTSTRAP_TOTP_ENV"] = source_settings.totp_env_var

    runner = command_runner or _default_command_runner
    command_result = runner(argv, run_env, resolved_root)
    if command_result.returncode != 0:
        output = _trim_output(command_result.stderr or command_result.stdout)
        _raise_challenge_error_if_detected(source=source, output=output, phase="invoke-command")
        raise BootstrapLoginError(
            source=source,
            step="invoke-command",
            reason=f"command exited with status {command_result.returncode}",
            details=output or "No stdout/stderr from bootstrap command.",
        )

    payload = _parse_bootstrap_output(source, command_result.stdout)
    _raise_challenge_error_if_detected(source=source, output=_collect_signal_text(payload), phase="authenticate")
    storage_state = _extract_storage_state(source, payload)

    try:
        session_path = save_session_state(
            source,
            storage_state,
            config=config,
            project_root=resolved_root,
        )
    except AuthenticationError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard around persistence.
        raise BootstrapLoginError(
            source=source,
            step="persist-session",
            reason="unable to persist storageState",
            details=str(exc),
        ) from exc

    exported_path: Path | None = None
    if export_path is not None:
        exported_path = export_session_state_json(
            source,
            export_path,
            config=config,
            project_root=resolved_root,
            now=now,
        )

    diagnostics = lookup_session_state(
        source,
        config=config,
        project_root=resolved_root,
        now=now,
    )
    return BootstrapSessionResult(
        source=source,
        headless=headless,
        bootstrap_command=resolved_command,
        session_state_path=str(session_path),
        export_path=str(exported_path) if exported_path is not None else None,
        expires_at=diagnostics.expires_at,
    )


def _default_command_runner(
    argv: list[str],
    environ: Mapping[str, str],
    cwd: Path,
) -> BootstrapCommandResult:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=dict(environ),
        capture_output=True,
        text=True,
        check=False,
    )
    return BootstrapCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_bootstrap_output(source: SupportedSource, stdout: str) -> dict[str, Any]:
    payload_text = stdout.strip()
    if not payload_text:
        raise BootstrapLoginError(
            source=source,
            step="parse-output",
            reason="bootstrap command produced empty stdout",
            details="Emit a JSON storageState object or {'storage_state': ...}.",
        )

    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise BootstrapLoginError(
            source=source,
            step="parse-output",
            reason="bootstrap command stdout is not valid JSON",
            details=f"JSON error: {exc.msg}",
        ) from exc

    if not isinstance(parsed, dict):
        raise BootstrapLoginError(
            source=source,
            step="parse-output",
            reason="bootstrap command stdout must be a JSON object",
        )
    return parsed


def _extract_storage_state(source: SupportedSource, payload: dict[str, Any]) -> dict[str, Any]:
    candidate = payload.get("storage_state", payload)
    if not isinstance(candidate, dict):
        raise BootstrapLoginError(
            source=source,
            step="parse-output",
            reason="storage_state payload must be a JSON object",
        )
    if "cookies" not in candidate or "origins" not in candidate:
        raise BootstrapLoginError(
            source=source,
            step="parse-output",
            reason="storageState payload must include 'cookies' and 'origins'",
        )
    return candidate


def _raise_challenge_error_if_detected(
    *,
    source: SupportedSource,
    output: str | None,
    phase: str,
) -> None:
    signal = (output or "").lower()
    if any(hint in signal for hint in _ANTI_BOT_HINTS):
        raise AntiBotChallengeError(
            source=source,
            details=(
                f"Detected anti-bot challenge during '{phase}'. "
                "Retry from a trusted local network and rerun bootstrap with --headful."
            ),
        )
    if any(hint in signal for hint in _MFA_HINTS):
        raise MFAChallengeError(
            source=source,
            details=(
                f"Detected MFA challenge during '{phase}'. "
                "Run bootstrap with --headful, complete verification, then export the session JSON."
            ),
        )


def _trim_output(output: str) -> str:
    text = output.strip()
    if len(text) <= 400:
        return text
    return text[:400].rstrip() + "..."


def _collect_signal_text(payload: dict[str, Any]) -> str:
    signals: list[str] = []
    for key in ("challenge", "error_type", "error", "status", "reason", "details", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            signals.append(value)
    return " ".join(signals)


def _normalize_optional_token(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _bootstrap_command_env_var(source: SupportedSource) -> str:
    if source == SupportedSource.linkedin:
        return "KB_ENRICHMENT_LINKEDIN_BOOTSTRAP_COMMAND"
    return "KB_ENRICHMENT_SKOOL_BOOTSTRAP_COMMAND"
