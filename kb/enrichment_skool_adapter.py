from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from kb.enrichment_adapters import (
    AntiBotChallengeError,
    AuthenticationRequest,
    AuthenticationResult,
    FetchRequest,
    FetchResult,
    MFAChallengeError,
    NormalizeRequest,
    NormalizeResult,
    NormalizedFact,
    SnapshotRequest,
    SnapshotResult,
    SourceAdapter,
    SourceAdapterError,
)
from kb.enrichment_bootstrap import bootstrap_session_login
from kb.enrichment_config import ConfidenceLevel, EnrichmentConfig, SupportedSource
from kb.enrichment_sessions import (
    SessionStateExpiredError,
    SessionStateMissingError,
    load_session_state,
    lookup_session_state,
)
from kb.schemas import KBBaseModel

SKOOL_FETCH_COMMAND_ENV_VAR = "KB_ENRICHMENT_SKOOL_FETCH_COMMAND"

_MFA_HINTS = ("mfa", "2fa", "two-factor", "verification code", "one-time code")
_ANTI_BOT_HINTS = (
    "captcha",
    "anti-bot",
    "bot challenge",
    "hcaptcha",
    "recaptcha",
    "cloudflare",
)
_UNSUPPORTED_STATUSES = {"unsupported", "not_supported", "not-supported", "unavailable"}


class SkoolExtractionError(SourceAdapterError):
    def __init__(self, *, reason: str, details: str | None = None) -> None:
        super().__init__(
            source=SupportedSource.skool,
            message=f"skool extraction failed: {reason}",
            details=details,
        )


class SkoolFetchCommandResult(KBBaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""


SkoolFetchRunner = Callable[[list[str], Mapping[str, str], Path], SkoolFetchCommandResult]
BootstrapLoginRunner = Callable[..., Any]


class SkoolSourceAdapter(SourceAdapter):
    source = SupportedSource.skool

    def __init__(
        self,
        *,
        config: EnrichmentConfig,
        project_root: Path,
        fetch_command: str | None = None,
        fetch_runner: SkoolFetchRunner | None = None,
        bootstrap_login_runner: BootstrapLoginRunner | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._config = config
        self._project_root = project_root.resolve()
        self._fetch_runner = fetch_runner or _default_fetch_runner
        self._bootstrap_login_runner = bootstrap_login_runner or bootstrap_session_login
        self._environ = dict(os.environ if environ is None else environ)
        default_fetch_command = _normalize_optional_token(self._config.sources[self.source].fetch_command)
        self._fetch_command = _normalize_optional_token(fetch_command) or _normalize_optional_token(
            self._environ.get(SKOOL_FETCH_COMMAND_ENV_VAR)
        ) or default_fetch_command

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult:
        auth_config = self._config_with_session_path(request.session_state_path)
        try:
            load_session_state(
                self.source,
                config=auth_config,
                project_root=self._project_root,
            )
        except (SessionStateMissingError, SessionStateExpiredError):
            self._bootstrap_login_runner(
                self.source,
                config=auth_config,
                project_root=self._project_root,
                headless=request.headless,
                environ=self._environ,
            )
            load_session_state(
                self.source,
                config=auth_config,
                project_root=self._project_root,
            )

        diagnostics = lookup_session_state(
            self.source,
            config=auth_config,
            project_root=self._project_root,
        )
        return AuthenticationResult(
            authenticated=True,
            used_session_state_path=request.session_state_path,
            expires_at=diagnostics.expires_at,
        )

    def fetch(self, request: FetchRequest) -> FetchResult:
        auth = self.authenticate(
            AuthenticationRequest(
                session_state_path=self._config.sources[self.source].session_state_path,
                headless=self._resolve_headless(),
            )
        )
        fetch_command = self._fetch_command
        if fetch_command is None:
            raise SkoolExtractionError(
                reason="fetch command not configured",
                details=(
                    f"set '{SKOOL_FETCH_COMMAND_ENV_VAR}', set source fetch_command in EnrichmentConfig, "
                    "or pass fetch_command explicitly"
                ),
            )

        argv = shlex.split(fetch_command)
        if not argv:
            raise SkoolExtractionError(
                reason="fetch command is empty",
                details="provide a non-empty skool extraction command",
            )

        run_env = dict(self._environ)
        run_env["KB_ENRICHMENT_EXTRACT_SOURCE"] = self.source.value
        run_env["KB_ENRICHMENT_EXTRACT_ENTITY_REF"] = request.entity_ref
        run_env["KB_ENRICHMENT_EXTRACT_ENTITY_SLUG"] = request.entity_slug
        run_env["KB_ENRICHMENT_EXTRACT_RUN_ID"] = request.run_id
        run_env["KB_ENRICHMENT_EXTRACT_SESSION_PATH"] = (
            auth.used_session_state_path or self._config.sources[self.source].session_state_path
        )
        run_env["KB_ENRICHMENT_EXTRACT_HEADLESS"] = "true" if self._resolve_headless() else "false"

        command_result = self._fetch_runner(argv, run_env, self._project_root)
        if command_result.returncode != 0:
            output = _trim_output(command_result.stderr or command_result.stdout)
            _raise_challenge_error_if_detected(source=self.source, signal=output, phase="fetch-command")
            raise SkoolExtractionError(
                reason=f"command exited with status {command_result.returncode}",
                details=output or "no stdout/stderr emitted by extraction command",
            )

        payload = _parse_fetch_payload(command_result.stdout)
        signal_text = _collect_signal_text(payload)
        _raise_challenge_error_if_detected(source=self.source, signal=signal_text, phase="fetch-payload")
        if _is_unsupported_payload(payload):
            raise SkoolExtractionError(
                reason="unsupported skool extraction flow",
                details=_trim_output(signal_text) or "payload indicated unsupported flow",
            )

        source_url = _resolve_source_url(payload, entity_slug=request.entity_slug)
        retrieved_at = _resolve_retrieved_at(payload, fallback=request.started_at)
        return FetchResult(
            source_url=source_url,
            retrieved_at=retrieved_at,
            payload=payload,
        )

    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        payload = request.fetch_result.payload
        facts = _facts_from_payload(payload, fetch_result=request.fetch_result)
        if not facts:
            raise SkoolExtractionError(
                reason="no facts extracted",
                details="payload did not include supported skool fact fields",
            )
        return NormalizeResult(facts=facts)

    def snapshot(self, request: SnapshotRequest) -> SnapshotResult:
        output_path = _resolve_output_path(Path(request.output_path), project_root=self._project_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        payload = request.fetch_result.payload
        html = payload.get("html")
        if isinstance(html, str) and html.strip():
            output_path.write_text(html, encoding="utf-8")
            return SnapshotResult(
                snapshot_path=request.output_path,
                content_type="text/html",
            )

        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return SnapshotResult(
            snapshot_path=request.output_path,
            content_type="application/json",
        )

    def _resolve_headless(self) -> bool:
        override = self._config.sources[self.source].headless_override
        if override is None:
            return self._config.headless_default
        return override

    def _config_with_session_path(self, session_state_path: str) -> EnrichmentConfig:
        configured_path = self._config.sources[self.source].session_state_path
        if session_state_path == configured_path:
            return self._config

        payload = self._config.model_dump(mode="python")
        payload["sources"][self.source]["session_state_path"] = session_state_path
        try:
            return EnrichmentConfig.model_validate(payload)
        except ValidationError as exc:
            raise SkoolExtractionError(
                reason="invalid session path override",
                details=str(exc),
            ) from exc


def _default_fetch_runner(argv: list[str], env: Mapping[str, str], cwd: Path) -> SkoolFetchCommandResult:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )
    return SkoolFetchCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_fetch_payload(stdout: str) -> dict[str, Any]:
    payload_text = stdout.strip()
    if not payload_text:
        raise SkoolExtractionError(
            reason="empty extraction output",
            details="extraction command must emit a JSON object",
        )

    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise SkoolExtractionError(
            reason="invalid extraction JSON",
            details=f"json parse error: {exc.msg}",
        ) from exc
    if not isinstance(parsed, dict):
        raise SkoolExtractionError(
            reason="invalid extraction payload",
            details="extraction payload must be a JSON object",
        )
    return parsed


def _facts_from_payload(payload: dict[str, Any], *, fetch_result: FetchResult) -> list[NormalizedFact]:
    facts_payload = payload.get("facts")
    if facts_payload is not None:
        if not isinstance(facts_payload, list):
            raise SkoolExtractionError(
                reason="invalid facts payload",
                details="'facts' must be a list when provided",
            )
        return _facts_from_list(facts_payload, fetch_result=fetch_result)

    profile = payload.get("profile")
    if profile is None:
        return []
    if not isinstance(profile, dict):
        raise SkoolExtractionError(
            reason="invalid profile payload",
            details="'profile' must be an object",
        )
    return _facts_from_profile(profile, payload=payload, fetch_result=fetch_result)


def _facts_from_list(facts_payload: list[Any], *, fetch_result: FetchResult) -> list[NormalizedFact]:
    facts: list[NormalizedFact] = []
    for index, raw_fact in enumerate(facts_payload):
        if not isinstance(raw_fact, dict):
            raise SkoolExtractionError(
                reason="invalid fact row",
                details=f"fact at index {index} must be an object",
            )
        attribute = _require_non_empty_text(raw_fact.get("attribute"), label=f"facts[{index}].attribute")
        value = _require_non_empty_text(raw_fact.get("value"), label=f"facts[{index}].value")
        confidence = _parse_confidence_level(
            raw_fact.get("confidence"),
            field=f"facts[{index}].confidence",
        )
        metadata = raw_fact.get("metadata")
        if metadata is None:
            normalized_metadata: dict[str, Any] = {}
        elif isinstance(metadata, dict):
            normalized_metadata = dict(metadata)
        else:
            raise SkoolExtractionError(
                reason="invalid fact metadata",
                details=f"facts[{index}].metadata must be an object when provided",
            )
        normalized_metadata.setdefault("adapter", SupportedSource.skool.value)
        normalized_metadata.setdefault("confidence_level", confidence.value)
        facts.append(
            NormalizedFact(
                attribute=attribute,
                value=value,
                confidence=confidence,
                source_url=fetch_result.source_url,
                retrieved_at=fetch_result.retrieved_at,
                metadata=normalized_metadata,
            )
        )
    return facts


def _facts_from_profile(
    profile: dict[str, Any],
    *,
    payload: dict[str, Any],
    fetch_result: FetchResult,
) -> list[NormalizedFact]:
    field_map = {
        "headline": "headline",
        "location": "location",
        "about": "about",
        "community": "community",
    }
    default_confidence = _parse_confidence_level(payload.get("confidence"), field="confidence")
    facts: list[NormalizedFact] = []
    for source_field, target_attribute in field_map.items():
        raw_value = profile.get(source_field)
        value = _normalize_optional_text(raw_value)
        if value is None:
            continue
        metadata = {
            "adapter": SupportedSource.skool.value,
            "profile_field": source_field,
            "confidence_level": default_confidence.value,
        }
        facts.append(
            NormalizedFact(
                attribute=target_attribute,
                value=value,
                confidence=default_confidence,
                source_url=fetch_result.source_url,
                retrieved_at=fetch_result.retrieved_at,
                metadata=metadata,
            )
        )
    return facts


def _parse_confidence_level(value: Any, *, field: str) -> ConfidenceLevel:
    if value is None:
        return ConfidenceLevel.medium
    if isinstance(value, ConfidenceLevel):
        return value
    if not isinstance(value, str):
        raise SkoolExtractionError(
            reason="invalid confidence value",
            details=f"{field} must be one of: low, medium, high",
        )
    try:
        return ConfidenceLevel(value.strip().lower())
    except ValueError as exc:
        raise SkoolExtractionError(
            reason="invalid confidence value",
            details=f"{field} must be one of: low, medium, high",
        ) from exc


def _resolve_source_url(payload: dict[str, Any], *, entity_slug: str) -> str:
    value = payload.get("source_url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"https://www.skool.com/@{entity_slug}"


def _resolve_retrieved_at(payload: dict[str, Any], *, fallback: datetime) -> datetime:
    value = payload.get("retrieved_at")
    if value is None:
        return _ensure_utc(fallback)
    if not isinstance(value, str) or not value.strip():
        raise SkoolExtractionError(
            reason="invalid retrieved timestamp",
            details="retrieved_at must be an ISO-8601 string when provided",
        )
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SkoolExtractionError(
            reason="invalid retrieved timestamp",
            details="retrieved_at must be an ISO-8601 string when provided",
        ) from exc
    return _ensure_utc(parsed)


def _is_unsupported_payload(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    if not isinstance(status, str):
        return False
    normalized = status.strip().lower().replace(" ", "_")
    return normalized in _UNSUPPORTED_STATUSES


def _collect_signal_text(payload: dict[str, Any]) -> str:
    signal_parts: list[str] = []
    for key in ("challenge", "error_type", "error", "status", "reason", "details", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            signal_parts.append(value)
    return " ".join(signal_parts)


def _raise_challenge_error_if_detected(
    *,
    source: SupportedSource,
    signal: str | None,
    phase: str,
) -> None:
    text = (signal or "").lower()
    if any(hint in text for hint in _ANTI_BOT_HINTS):
        raise AntiBotChallengeError(
            source=source,
            details=(
                f"Detected anti-bot challenge during '{phase}'. "
                "Retry in headful mode and re-export a trusted session."
            ),
        )
    if any(hint in text for hint in _MFA_HINTS):
        raise MFAChallengeError(
            source=source,
            details=(
                f"Detected MFA challenge during '{phase}'. "
                "Run bootstrap with --headful, complete verification, then retry extraction."
            ),
        )


def _trim_output(value: str) -> str:
    text = value.strip()
    if len(text) <= 400:
        return text
    return text[:400].rstrip() + "..."


def _normalize_optional_token(value: str | None) -> str | None:
    if value is None:
        return None
    token = value.strip()
    if not token:
        return None
    return token


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    return token


def _require_non_empty_text(value: Any, *, label: str) -> str:
    token = _normalize_optional_text(value)
    if token is None:
        raise SkoolExtractionError(
            reason="invalid fact payload",
            details=f"{label} must be a non-empty string",
        )
    return token


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _resolve_output_path(output_path: Path, *, project_root: Path) -> Path:
    if output_path.is_absolute():
        return output_path
    return project_root.joinpath(output_path)
