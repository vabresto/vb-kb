from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from kb.schemas import KBBaseModel

DEFAULT_ENRICHMENT_ROOT = ".build/enrichment"
DEFAULT_RUN_REPORT_PATH = f"{DEFAULT_ENRICHMENT_ROOT}/reports/latest-run.json"
DEFAULT_SESSION_ROOT = f"{DEFAULT_ENRICHMENT_ROOT}/sessions"
DEFAULT_SOURCE_EVIDENCE_ROOT = f"{DEFAULT_ENRICHMENT_ROOT}/source-evidence"


class SupportedSource(str, Enum):
    linkedin = "linkedin.com"
    skool = "skool.com"


class ConfidenceLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class SecretProvider(str, Enum):
    local = "local"
    env = "env"


def _validate_relative_path(value: str) -> str:
    path = value.strip()
    if not path:
        raise ValueError("path must be non-empty")
    if path.startswith("/"):
        raise ValueError("path must be relative")
    if ".." in path.split("/"):
        raise ValueError("path cannot contain '..'")
    return path


def _parse_bool(raw: str, *, env_var: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{env_var} must be a boolean value")


def _normalize_optional_token(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text


class ConfidencePolicy(KBBaseModel):
    minimum_promotion_level: ConfidenceLevel = ConfidenceLevel.medium
    low_min_score: float = 0.35
    medium_min_score: float = 0.65
    high_min_score: float = 0.85

    @field_validator("low_min_score", "medium_min_score", "high_min_score")
    @classmethod
    def validate_score_range(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("scores must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_ordering(self) -> "ConfidencePolicy":
        if self.low_min_score > self.medium_min_score:
            raise ValueError("low_min_score must be <= medium_min_score")
        if self.medium_min_score > self.high_min_score:
            raise ValueError("medium_min_score must be <= high_min_score")
        return self

    def score_for_level(self, level: ConfidenceLevel) -> float:
        if level == ConfidenceLevel.low:
            return self.low_min_score
        if level == ConfidenceLevel.medium:
            return self.medium_min_score
        return self.high_min_score


class SecretProviderPolicy(KBBaseModel):
    provider: SecretProvider = SecretProvider.local
    env_var_fallback: bool = True


class SourceSettings(KBBaseModel):
    session_state_path: str
    evidence_path: str
    headless_override: bool | None = None
    username_env_var: str | None = None
    password_env_var: str | None = None
    username_secret_ref: str | None = None
    password_secret_ref: str | None = None

    @field_validator("session_state_path", "evidence_path")
    @classmethod
    def validate_paths(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator(
        "username_env_var",
        "password_env_var",
        "username_secret_ref",
        "password_secret_ref",
    )
    @classmethod
    def normalize_optional_tokens(cls, value: str | None) -> str | None:
        return _normalize_optional_token(value)


def default_source_settings() -> dict[SupportedSource, SourceSettings]:
    return {
        SupportedSource.linkedin: SourceSettings(
            session_state_path=f"{DEFAULT_SESSION_ROOT}/linkedin.com/storage-state.json",
            evidence_path=f"{DEFAULT_SOURCE_EVIDENCE_ROOT}/linkedin.com",
            username_env_var="KB_ENRICH_LINKEDIN_USERNAME",
            password_env_var="KB_ENRICH_LINKEDIN_PASSWORD",
            username_secret_ref="linkedin.username",
            password_secret_ref="linkedin.password",
        ),
        SupportedSource.skool: SourceSettings(
            session_state_path=f"{DEFAULT_SESSION_ROOT}/skool.com/storage-state.json",
            evidence_path=f"{DEFAULT_SOURCE_EVIDENCE_ROOT}/skool.com",
            username_env_var="KB_ENRICH_SKOOL_USERNAME",
            password_env_var="KB_ENRICH_SKOOL_PASSWORD",
            username_secret_ref="skool.username",
            password_secret_ref="skool.password",
        ),
    }


class EnrichmentConfig(KBBaseModel):
    headless_default: bool = True
    run_report_path: str = DEFAULT_RUN_REPORT_PATH
    confidence_policy: ConfidencePolicy = Field(default_factory=ConfidencePolicy)
    secret_provider: SecretProviderPolicy = Field(default_factory=SecretProviderPolicy)
    sources: dict[SupportedSource, SourceSettings] = Field(default_factory=default_source_settings)

    @field_validator("run_report_path")
    @classmethod
    def validate_report_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @model_validator(mode="after")
    def validate_sources(self) -> "EnrichmentConfig":
        required_sources = set(SupportedSource)
        configured_sources = set(self.sources.keys())
        if configured_sources != required_sources:
            missing = sorted(source.value for source in required_sources - configured_sources)
            extra = sorted(source.value for source in configured_sources - required_sources)
            details: list[str] = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if extra:
                details.append(f"extra: {', '.join(extra)}")
            raise ValueError("sources must include linkedin.com and skool.com only" + f" ({'; '.join(details)})")
        return self


def load_enrichment_config_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    base_config: EnrichmentConfig | None = None,
) -> EnrichmentConfig:
    env = dict(os.environ if environ is None else environ)
    config = base_config or EnrichmentConfig()
    payload = config.model_dump(mode="python")

    if "KB_ENRICHMENT_HEADLESS_DEFAULT" in env:
        payload["headless_default"] = _parse_bool(
            env["KB_ENRICHMENT_HEADLESS_DEFAULT"],
            env_var="KB_ENRICHMENT_HEADLESS_DEFAULT",
        )
    if "KB_ENRICHMENT_RUN_REPORT_PATH" in env:
        payload["run_report_path"] = env["KB_ENRICHMENT_RUN_REPORT_PATH"]
    if "KB_ENRICHMENT_CONFIDENCE_MINIMUM" in env:
        payload["confidence_policy"]["minimum_promotion_level"] = env[
            "KB_ENRICHMENT_CONFIDENCE_MINIMUM"
        ].strip()
    if "KB_ENRICHMENT_SECRET_PROVIDER" in env:
        payload["secret_provider"]["provider"] = env["KB_ENRICHMENT_SECRET_PROVIDER"].strip()
    if "KB_ENRICHMENT_SECRET_ENV_FALLBACK" in env:
        payload["secret_provider"]["env_var_fallback"] = _parse_bool(
            env["KB_ENRICHMENT_SECRET_ENV_FALLBACK"],
            env_var="KB_ENRICHMENT_SECRET_ENV_FALLBACK",
        )

    source_env = {
        SupportedSource.linkedin: {
            "session_state_path": "KB_ENRICHMENT_LINKEDIN_SESSION_PATH",
            "evidence_path": "KB_ENRICHMENT_LINKEDIN_EVIDENCE_PATH",
            "username_env_var": "KB_ENRICHMENT_LINKEDIN_USERNAME_ENV",
            "password_env_var": "KB_ENRICHMENT_LINKEDIN_PASSWORD_ENV",
            "username_secret_ref": "KB_ENRICHMENT_LINKEDIN_USERNAME_SECRET",
            "password_secret_ref": "KB_ENRICHMENT_LINKEDIN_PASSWORD_SECRET",
        },
        SupportedSource.skool: {
            "session_state_path": "KB_ENRICHMENT_SKOOL_SESSION_PATH",
            "evidence_path": "KB_ENRICHMENT_SKOOL_EVIDENCE_PATH",
            "username_env_var": "KB_ENRICHMENT_SKOOL_USERNAME_ENV",
            "password_env_var": "KB_ENRICHMENT_SKOOL_PASSWORD_ENV",
            "username_secret_ref": "KB_ENRICHMENT_SKOOL_USERNAME_SECRET",
            "password_secret_ref": "KB_ENRICHMENT_SKOOL_PASSWORD_SECRET",
        },
    }

    for source, mapping in source_env.items():
        source_payload = payload["sources"][source]
        for field_name, env_var in mapping.items():
            if env_var in env:
                source_payload[field_name] = env[env_var]
        headless_env_var = (
            "KB_ENRICHMENT_LINKEDIN_HEADLESS_OVERRIDE"
            if source == SupportedSource.linkedin
            else "KB_ENRICHMENT_SKOOL_HEADLESS_OVERRIDE"
        )
        if headless_env_var in env:
            source_payload["headless_override"] = _parse_bool(
                env[headless_env_var],
                env_var=headless_env_var,
            )

    return EnrichmentConfig.model_validate(payload)
