from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kb.cli import build_parser, run_person_init
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_run import (
    EnrichmentRunReport,
    ExtractionPhaseState,
    PhaseState,
    PhaseStatus,
    RunPhaseStates,
    RunStatus,
    SourceExtractionState,
)


def _url(value: str) -> str:
    return value.replace("hxxps://", "https://")


def _write_person_template(project_root: Path) -> None:
    template_path = project_root / "data" / "person" / "_template" / "index.md"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        "\n".join(
            [
                "---",
                "person: {{PERSON_NAME}}",
                "created-at: {{TODAY}}",
                "updated-at: {{TODAY}}",
                "linkedin: null",
                "---",
                "",
                "# {{PERSON_NAME}}",
                "",
                "## Snapshot",
                "",
                "- Why they matter: TBD.",
                "",
                "## Bio",
                "",
                "{{PERSON_NAME}} profile initialized from scaffold.",
                "",
                "## Conversation Notes",
                "",
                "- [{{TODAY}}] Added baseline profile scaffold.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _stub_report(
    *,
    sources: list[SupportedSource],
    extraction_sources: list[SourceExtractionState] | None = None,
) -> EnrichmentRunReport:
    now = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    return EnrichmentRunReport(
        run_id="enrich-person-init-stub",
        entity_ref="person@jose-luis-avilez",
        entity_slug="jose-luis-avilez",
        selected_sources=sources,
        status=RunStatus.succeeded,
        started_at=now,
        completed_at=now,
        facts_extracted_total=3,
        report_path=".build/enrichment/reports/latest-run.json",
        phases=RunPhaseStates(
            extraction=ExtractionPhaseState(
                status=PhaseStatus.succeeded,
                message="extraction completed",
                sources=list(extraction_sources or []),
            ),
            source_logging=PhaseState(
                status=PhaseStatus.succeeded,
                message="source logging completed",
            ),
            mapping=PhaseState(
                status=PhaseStatus.succeeded,
                message="mapping completed",
            ),
            validation=PhaseState(
                status=PhaseStatus.succeeded,
                message="validation completed",
            ),
            reporting=PhaseState(
                status=PhaseStatus.succeeded,
                message="report written",
            ),
        ),
    )


def _write_facts_artifact(
    *,
    tmp_path: Path,
    source: SupportedSource,
    slug: str,
    image_url: str | None,
) -> str:
    artifact_dir = tmp_path / "data" / "source" / "en" / f"source@enrichment-{source.value.replace('.', '-')}-{slug}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "facts.json"
    facts: list[dict[str, object]] = []
    if image_url is not None:
        facts.append(
            {
                "attribute": "profile_image_url",
                "value": image_url,
                "confidence": "low",
                "metadata": {"source_section": "profile_meta"},
                "source_url": f"https://{source.value}/in/{slug}",
                "retrieved_at": "2026-03-02T12:00:00+00:00",
            }
        )
    artifact_payload = {
        "source": source.value,
        "entity_slug": slug,
        "facts": facts,
    }
    artifact_path.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact_path.relative_to(tmp_path).as_posix()


def test_person_init_parser_accepts_profile_url_flags() -> None:
    parser = build_parser()
    linkedin_url = _url("hxxps://www.linkedin.com/in/jose-luis-avilez/")
    skool_url = _url("hxxps://www.skool.com/@jose.avilez")
    args = parser.parse_args(
        [
            "person-init",
            "--slug",
            "jose-luis-avilez",
            "--name",
            "Jose Luis Avilez",
            "--linkedin-url",
            linkedin_url,
            "--skool-url",
            skool_url,
            "--intro-note",
            "shared skool community",
            "--how-we-met",
            "met at demo day",
            "--why-added",
            "potential collaborator",
            "--headful",
        ]
    )

    assert args.command == "person-init"
    assert args.slug == "jose-luis-avilez"
    assert args.name == "Jose Luis Avilez"
    assert args.linkedin_url == linkedin_url
    assert args.skool_url == skool_url
    assert args.intro_note == "shared skool community"
    assert args.how_we_met == "met at demo day"
    assert args.why_added == "potential collaborator"
    assert args.headful is True


def test_run_person_init_scaffolds_and_bootstraps_from_both_sources(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_person_template(tmp_path)
    observed: dict[str, object] = {}

    def _run_stub(
        entity_target: str,
        *,
        selected_sources,
        source_url_overrides,
        config: EnrichmentConfig,
        project_root: Path,
        environ=None,
    ) -> EnrichmentRunReport:
        observed["entity_target"] = entity_target
        observed["selected_sources"] = list(selected_sources or [])
        observed["source_url_overrides"] = dict(source_url_overrides or {})
        observed["project_root"] = project_root
        observed["random_waits"] = (environ or {}).get("KB_ENRICHMENT_ACTION_RANDOM_WAITS")
        assert isinstance(config, EnrichmentConfig)
        return _stub_report(sources=[SupportedSource.linkedin, SupportedSource.skool])

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.run_enrichment_for_entity", _run_stub)

    parser = build_parser()
    linkedin_input_url = _url("hxxps://www.linkedin.com/in/jose-luis-avilez/details/experience/?trk=public_profile")
    skool_input_url = _url("hxxps://www.skool.com/@jose.avilez/community")
    linkedin_canonical_url = _url("hxxps://www.linkedin.com/in/jose-luis-avilez/")
    skool_canonical_url = _url("hxxps://www.skool.com/@jose.avilez")
    args = parser.parse_args(
        [
            "person-init",
            "--linkedin-url",
            linkedin_input_url,
            "--skool-url",
            skool_input_url,
            "--intro-note",
            "shared skool community",
            "--how-we-met",
            "met at demo day",
            "--project-root",
            str(tmp_path),
            "--no-random-waits",
        ]
    )
    status_code = run_person_init(args)

    assert status_code == 0
    person_index = tmp_path / "data" / "person" / "jo" / "person@jose-luis-avilez" / "index.md"
    assert person_index.exists()
    person_markdown = person_index.read_text(encoding="utf-8")
    assert "person: Jose Luis Avilez" in person_markdown
    assert "how-we-met: met at demo day" in person_markdown
    assert "why-added: shared skool community" in person_markdown

    assert observed["entity_target"] == "person@jose-luis-avilez"
    assert observed["selected_sources"] == [SupportedSource.linkedin, SupportedSource.skool]
    assert observed["source_url_overrides"] == {
        SupportedSource.linkedin: linkedin_canonical_url,
        SupportedSource.skool: skool_canonical_url,
    }
    assert observed["project_root"] == tmp_path.resolve()
    assert observed["random_waits"] == "false"

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["created"] is True
    assert payload["enrichment_triggered"] is True
    assert payload["selected_sources"] == ["linkedin.com", "skool.com"]
    assert payload["source_url_overrides"] == {
        "linkedin.com": linkedin_canonical_url,
        "skool.com": skool_canonical_url,
    }
    assert payload["intro_notes"] == {
        "how-we-met": "met at demo day",
        "why-added": "shared skool community",
    }


def test_run_person_init_downloads_profile_image_preferring_linkedin(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_person_template(tmp_path)

    slug = "jose-luis-avilez"
    linkedin_image_url = _url("hxxps://media.licdn.com/dms/image/v2/abc/profile-displayphoto-shrink_800_800.jpg")
    skool_image_url = _url("hxxps://cdn.skool.com/profile/jose.png")
    linkedin_artifact_path = _write_facts_artifact(
        tmp_path=tmp_path,
        source=SupportedSource.linkedin,
        slug=slug,
        image_url=linkedin_image_url,
    )
    skool_artifact_path = _write_facts_artifact(
        tmp_path=tmp_path,
        source=SupportedSource.skool,
        slug=slug,
        image_url=skool_image_url,
    )

    def _run_stub(
        _entity_target: str,
        *,
        selected_sources,
        source_url_overrides,
        config: EnrichmentConfig,
        project_root: Path,
        environ=None,
    ) -> EnrichmentRunReport:
        assert list(selected_sources or []) == [SupportedSource.linkedin, SupportedSource.skool]
        assert dict(source_url_overrides or {})
        assert isinstance(config, EnrichmentConfig)
        assert project_root == tmp_path.resolve()
        assert environ is None
        return _stub_report(
            sources=[SupportedSource.linkedin, SupportedSource.skool],
            extraction_sources=[
                SourceExtractionState(
                    source=SupportedSource.linkedin,
                    status=PhaseStatus.succeeded,
                    facts_artifact_path=linkedin_artifact_path,
                ),
                SourceExtractionState(
                    source=SupportedSource.skool,
                    status=PhaseStatus.succeeded,
                    facts_artifact_path=skool_artifact_path,
                ),
            ],
        )

    observed_download_url: dict[str, str] = {}

    def _download_stub(url: str) -> tuple[bytes, str]:
        observed_download_url["url"] = url
        return (b"fake-image-bytes", "jpg")

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.run_enrichment_for_entity", _run_stub)
    monkeypatch.setattr("kb.cli._download_profile_image", _download_stub)

    parser = build_parser()
    args = parser.parse_args(
        [
            "person-init",
            "--linkedin-url",
            _url("hxxps://www.linkedin.com/in/jose-luis-avilez/"),
            "--skool-url",
            _url("hxxps://www.skool.com/@jose.avilez"),
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_person_init(args)

    assert status_code == 0
    assert observed_download_url["url"] == linkedin_image_url

    image_path = tmp_path / "data" / "person" / "jo" / "person@jose-luis-avilez" / "images" / "jose-luis-avilez.jpg"
    assert image_path.exists()
    assert image_path.read_bytes() == b"fake-image-bytes"

    person_index = tmp_path / "data" / "person" / "jo" / "person@jose-luis-avilez" / "index.md"
    person_markdown = person_index.read_text(encoding="utf-8")
    assert "![Headshot of Jose Luis Avilez](./images/jose-luis-avilez.jpg)" in person_markdown

    payload = json.loads(capsys.readouterr().out)
    assert payload["profile_image"]["saved"] is True
    assert payload["profile_image"]["source"] == "linkedin.com"
    assert payload["profile_image"]["url"] == linkedin_image_url


def test_run_person_init_downloads_profile_image_from_skool_when_linkedin_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_person_template(tmp_path)

    slug = "jose-luis-avilez"
    skool_image_url = _url("hxxps://cdn.skool.com/profile/jose.png")
    linkedin_artifact_path = _write_facts_artifact(
        tmp_path=tmp_path,
        source=SupportedSource.linkedin,
        slug=slug,
        image_url=None,
    )
    skool_artifact_path = _write_facts_artifact(
        tmp_path=tmp_path,
        source=SupportedSource.skool,
        slug=slug,
        image_url=skool_image_url,
    )

    def _run_stub(
        _entity_target: str,
        *,
        selected_sources,
        source_url_overrides,
        config: EnrichmentConfig,
        project_root: Path,
        environ=None,
    ) -> EnrichmentRunReport:
        assert list(selected_sources or []) == [SupportedSource.linkedin, SupportedSource.skool]
        assert dict(source_url_overrides or {})
        assert isinstance(config, EnrichmentConfig)
        assert project_root == tmp_path.resolve()
        assert environ is None
        return _stub_report(
            sources=[SupportedSource.linkedin, SupportedSource.skool],
            extraction_sources=[
                SourceExtractionState(
                    source=SupportedSource.linkedin,
                    status=PhaseStatus.succeeded,
                    facts_artifact_path=linkedin_artifact_path,
                ),
                SourceExtractionState(
                    source=SupportedSource.skool,
                    status=PhaseStatus.succeeded,
                    facts_artifact_path=skool_artifact_path,
                ),
            ],
        )

    observed_download_url: dict[str, str] = {}

    def _download_stub(url: str) -> tuple[bytes, str]:
        observed_download_url["url"] = url
        return (b"fake-image-bytes", "png")

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.run_enrichment_for_entity", _run_stub)
    monkeypatch.setattr("kb.cli._download_profile_image", _download_stub)

    parser = build_parser()
    args = parser.parse_args(
        [
            "person-init",
            "--linkedin-url",
            _url("hxxps://www.linkedin.com/in/jose-luis-avilez/"),
            "--skool-url",
            _url("hxxps://www.skool.com/@jose.avilez"),
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_person_init(args)

    assert status_code == 0
    assert observed_download_url["url"] == skool_image_url

    image_path = tmp_path / "data" / "person" / "jo" / "person@jose-luis-avilez" / "images" / "jose-luis-avilez.png"
    assert image_path.exists()
    assert image_path.read_bytes() == b"fake-image-bytes"

    payload = json.loads(capsys.readouterr().out)
    assert payload["profile_image"]["saved"] is True
    assert payload["profile_image"]["source"] == "skool.com"
    assert payload["profile_image"]["url"] == skool_image_url


def test_run_person_init_scaffolds_without_enrichment_when_no_profile_urls(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_person_template(tmp_path)
    monkeypatch.setattr("kb.cli.run_enrichment_for_entity", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError))

    parser = build_parser()
    args = parser.parse_args(
        [
            "person-init",
            "--slug",
            "jane-founder",
            "--name",
            "Jane Founder",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_person_init(args)

    assert status_code == 0
    person_index = tmp_path / "data" / "person" / "ja" / "person@jane-founder" / "index.md"
    assert person_index.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["created"] is True
    assert payload["enrichment_triggered"] is False
    assert payload["selected_sources"] == []


def test_run_person_init_rejects_invalid_profile_url_domain(
    tmp_path: Path,
    capsys,
) -> None:
    _write_person_template(tmp_path)

    parser = build_parser()
    args = parser.parse_args(
        [
            "person-init",
            "--linkedin-url",
            _url("hxxps://example.com/not-linkedin"),
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_person_init(args)

    assert status_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "linkedin.com" in payload["message"]


def test_run_person_init_updates_intro_notes_for_existing_person(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_person_template(tmp_path)
    monkeypatch.setattr("kb.cli.run_enrichment_for_entity", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError))
    parser = build_parser()

    first_args = parser.parse_args(
        [
            "person-init",
            "--slug",
            "jane-founder",
            "--name",
            "Jane Founder",
            "--project-root",
            str(tmp_path),
        ]
    )
    assert run_person_init(first_args) == 0
    _ = capsys.readouterr()

    second_args = parser.parse_args(
        [
            "person-init",
            "--slug",
            "jane-founder",
            "--project-root",
            str(tmp_path),
            "--intro-note",
            "shared skool community",
            "--how-we-met",
            "met at fintech meetup",
        ]
    )
    status_code = run_person_init(second_args)

    assert status_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["created"] is False
    assert sorted(payload["frontmatter_fields_updated"]) == ["how-we-met", "why-added"]
    assert payload["intro_notes"] == {
        "how-we-met": "met at fintech meetup",
        "why-added": "shared skool community",
    }

    person_index = tmp_path / "data" / "person" / "ja" / "person@jane-founder" / "index.md"
    person_markdown = person_index.read_text(encoding="utf-8")
    assert "how-we-met: met at fintech meetup" in person_markdown
    assert "why-added: shared skool community" in person_markdown
