from __future__ import annotations

import pytest

from kb.enrichment_config import (
    DEFAULT_LINKEDIN_FETCH_COMMAND,
    DEFAULT_LINKEDIN_BOOTSTRAP_COMMAND,
    DEFAULT_RUN_REPORT_PATH,
    DEFAULT_SKOOL_FETCH_COMMAND,
    DEFAULT_SKOOL_BOOTSTRAP_COMMAND,
    EnrichmentConfig,
    SecretProvider,
    SupportedSource,
    load_enrichment_config_from_env,
)


def test_default_enrichment_config_includes_supported_sources_and_artifacts() -> None:
    config = EnrichmentConfig()

    assert config.headless_default is True
    assert config.run_report_path == DEFAULT_RUN_REPORT_PATH
    assert config.run_report_path.startswith(".build/")
    assert config.secret_provider.provider == SecretProvider.local
    assert config.secret_provider.env_var_fallback is True

    assert set(config.sources.keys()) == {SupportedSource.linkedin, SupportedSource.skool}
    linkedin = config.sources[SupportedSource.linkedin]
    skool = config.sources[SupportedSource.skool]

    assert linkedin.session_state_path.startswith(".build/")
    assert linkedin.evidence_path.startswith(".build/")
    assert linkedin.bootstrap_command == DEFAULT_LINKEDIN_BOOTSTRAP_COMMAND
    assert linkedin.fetch_command == DEFAULT_LINKEDIN_FETCH_COMMAND
    assert skool.session_state_path.startswith(".build/")
    assert skool.evidence_path.startswith(".build/")
    assert skool.bootstrap_command == DEFAULT_SKOOL_BOOTSTRAP_COMMAND
    assert skool.fetch_command == DEFAULT_SKOOL_FETCH_COMMAND
    assert "linkedin.com" in linkedin.session_state_path
    assert "skool.com" in skool.session_state_path


def test_enrichment_config_rejects_unsupported_or_missing_sources() -> None:
    with pytest.raises(ValueError, match="sources must include linkedin.com and skool.com only"):
        EnrichmentConfig.model_validate(
            {
                "sources": {
                    "linkedin.com": {
                        "session_state_path": ".build/enrichment/sessions/linkedin.com/storage-state.json",
                        "evidence_path": ".build/enrichment/source-evidence/linkedin.com",
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="Input should be 'linkedin.com' or 'skool.com'"):
        EnrichmentConfig.model_validate(
            {
                "sources": {
                    "linkedin.com": {
                        "session_state_path": ".build/enrichment/sessions/linkedin.com/storage-state.json",
                        "evidence_path": ".build/enrichment/source-evidence/linkedin.com",
                    },
                    "skool.com": {
                        "session_state_path": ".build/enrichment/sessions/skool.com/storage-state.json",
                        "evidence_path": ".build/enrichment/source-evidence/skool.com",
                    },
                    "example.com": {
                        "session_state_path": ".build/enrichment/sessions/example.com/storage-state.json",
                        "evidence_path": ".build/enrichment/source-evidence/example.com",
                    },
                }
            }
        )


def test_enrichment_config_paths_must_be_relative() -> None:
    with pytest.raises(ValueError, match="path must be relative"):
        EnrichmentConfig.model_validate(
            {
                "run_report_path": "/tmp/report.json",
            }
        )


def test_load_enrichment_config_from_env_overrides_defaults() -> None:
    config = load_enrichment_config_from_env(
        {
            "KB_ENRICHMENT_HEADLESS_DEFAULT": "false",
            "KB_ENRICHMENT_RUN_REPORT_PATH": ".build/enrichment/reports/manual-run.json",
            "KB_ENRICHMENT_CONFIDENCE_MINIMUM": "high",
            "KB_ENRICHMENT_SECRET_PROVIDER": "env",
            "KB_ENRICHMENT_SECRET_ENV_FALLBACK": "true",
            "KB_ENRICHMENT_LINKEDIN_SESSION_PATH": ".build/enrichment/sessions/linkedin.com/custom.json",
            "KB_ENRICHMENT_LINKEDIN_EVIDENCE_PATH": ".build/enrichment/source-evidence/linkedin.com/custom",
            "KB_ENRICHMENT_LINKEDIN_BOOTSTRAP_COMMAND": "scripts/bootstrap-linkedin.sh",
            "KB_ENRICHMENT_LINKEDIN_FETCH_COMMAND": "scripts/fetch-linkedin.sh",
            "KB_ENRICHMENT_LINKEDIN_HEADLESS_OVERRIDE": "true",
            "KB_ENRICHMENT_SKOOL_SESSION_PATH": ".build/enrichment/sessions/skool.com/custom.json",
            "KB_ENRICHMENT_SKOOL_EVIDENCE_PATH": ".build/enrichment/source-evidence/skool.com/custom",
            "KB_ENRICHMENT_SKOOL_BOOTSTRAP_COMMAND": "scripts/bootstrap-skool.sh",
            "KB_ENRICHMENT_SKOOL_FETCH_COMMAND": "scripts/fetch-skool.sh",
        }
    )

    assert config.headless_default is False
    assert config.run_report_path == ".build/enrichment/reports/manual-run.json"
    assert config.confidence_policy.minimum_promotion_level.value == "high"
    assert config.secret_provider.provider == SecretProvider.env
    assert config.secret_provider.env_var_fallback is True

    linkedin = config.sources[SupportedSource.linkedin]
    skool = config.sources[SupportedSource.skool]
    assert linkedin.session_state_path.endswith("custom.json")
    assert linkedin.bootstrap_command == "scripts/bootstrap-linkedin.sh"
    assert linkedin.fetch_command == "scripts/fetch-linkedin.sh"
    assert linkedin.headless_override is True
    assert skool.session_state_path.endswith("custom.json")
    assert skool.bootstrap_command == "scripts/bootstrap-skool.sh"
    assert skool.fetch_command == "scripts/fetch-skool.sh"


def test_confidence_policy_requires_monotonic_thresholds() -> None:
    with pytest.raises(ValueError, match="medium_min_score must be <= high_min_score"):
        EnrichmentConfig.model_validate(
            {
                "confidence_policy": {
                    "low_min_score": 0.3,
                    "medium_min_score": 0.8,
                    "high_min_score": 0.7,
                }
            }
        )
