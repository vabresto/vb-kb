from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from kb.enrichment_adapters import (
    AuthenticationRequest,
    AuthenticationResult,
    FetchRequest,
    FetchResult,
    NormalizeRequest,
    NormalizeResult,
    NormalizedFact,
    SnapshotRequest,
    SnapshotResult,
    SourceAdapter,
    SourceAdapterError,
    SourceAdapterRegistry,
)
from kb.enrichment_config import ConfidenceLevel, EnrichmentConfig, SupportedSource
from kb.enrichment_run import (
    EntityTargetResolutionError,
    PhaseStatus,
    RunStatus,
    resolve_entity_target,
    run_enrichment_for_entity,
)
from kb.schemas import shard_for_slug


_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n?(?P<body>.*)\Z", re.DOTALL)


class _SuccessfulAdapter(SourceAdapter):
    def __init__(self, source: SupportedSource, *, project_root: Path) -> None:
        self.source = source
        self._project_root = project_root

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult:
        return AuthenticationResult(
            authenticated=True,
            used_session_state_path=request.session_state_path,
        )

    def fetch(self, request: FetchRequest) -> FetchResult:
        return FetchResult(
            source_url=f"https://{self.source.value}/entity/{request.entity_slug}",
            retrieved_at=datetime(2026, 2, 28, 17, 0, tzinfo=UTC),
            payload={},
        )

    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        return NormalizeResult(
            facts=[
                NormalizedFact(
                    attribute="headline",
                    value=f"{self.source.value} headline",
                    confidence=ConfidenceLevel.medium,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                )
            ]
        )

    def snapshot(self, request: SnapshotRequest) -> SnapshotResult:
        output_path = self._project_root / request.output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}", encoding="utf-8")
        return SnapshotResult(
            snapshot_path=request.output_path,
            content_type="application/json",
        )


class _FailingAdapter(_SuccessfulAdapter):
    def fetch(self, request: FetchRequest) -> FetchResult:
        raise SourceAdapterError(source=self.source, message="simulated extraction failure")


def _write_person_fixture(
    project_root: Path,
    *,
    slug: str = "founder-name",
    firm: str = "Legacy Labs",
    role: str = "Founder",
    location: str = "Kitchener, ON",
) -> Path:
    shard = shard_for_slug(slug)
    person_dir = project_root / "data" / "person" / shard / f"person@{slug}"
    person_dir.mkdir(parents=True, exist_ok=True)
    (person_dir / "edges").mkdir(parents=True, exist_ok=True)
    (person_dir / "edges" / ".gitkeep").write_text("", encoding="utf-8")
    (person_dir / "changelog.jsonl").write_text("", encoding="utf-8")
    (person_dir / "looking-for.jsonl").write_text("", encoding="utf-8")
    (person_dir / "employment-history.jsonl").write_text(
        json.dumps(
            {
                "id": "employment-001",
                "period": "2020 - 2024",
                "organization": "Prior Co",
                "role": "Engineer",
                "notes": "Seeded historical row.",
                "source_path": f"data/person/{shard}/person@{slug}/index.md",
                "source_section": "employment_history_table",
                "source_row": 1,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    index_path = person_dir / "index.md"
    index_path.write_text(
        "\n".join(
            [
                "---",
                "person: Founder Name",
                f"firm: {firm}",
                f"role: {role}",
                f"location: {location}",
                "updated-at: 2026-02-20",
                "---",
                "",
                "# Founder Name",
                "",
                "## Snapshot",
                "",
                "- Why they matter: Works with [Legacy Labs](../../../org/le/org@legacy-labs/index.md).",
                "",
                "## Bio",
                "",
                "Founder profile references [Legacy Labs](../../../org/le/org@legacy-labs/index.md).",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return index_path


def _read_frontmatter_and_body(index_path: Path) -> tuple[dict[str, object], str]:
    markdown = index_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_BLOCK_RE.match(markdown)
    assert match is not None
    payload = yaml.safe_load(match.group("frontmatter"))
    assert isinstance(payload, dict)
    return payload, match.group("body")


def test_resolve_entity_target_accepts_slug_and_canonical_path() -> None:
    slug_target = resolve_entity_target("founder-name")
    assert slug_target.entity_ref == "founder-name"
    assert slug_target.entity_slug == "founder-name"

    path_target = resolve_entity_target("data/person/fo/person@founder-name/index.md")
    assert path_target.entity_ref == "data/person/fo/person@founder-name/index.md"
    assert path_target.entity_slug == "founder-name"


def test_resolve_entity_target_rejects_invalid_value() -> None:
    with pytest.raises(EntityTargetResolutionError):
        resolve_entity_target("Founder Name")


def test_run_enrichment_for_entity_reports_all_phase_states(tmp_path: Path) -> None:
    person_index_path = _write_person_fixture(tmp_path)
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _SuccessfulAdapter(SupportedSource.linkedin, project_root=tmp_path),
            _SuccessfulAdapter(SupportedSource.skool, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "founder-name",
        selected_sources=[SupportedSource.linkedin, SupportedSource.skool],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        now=datetime(2026, 2, 28, 17, 5, tzinfo=UTC),
        run_id="enrich-test-run",
    )

    assert report.status == RunStatus.partial
    assert report.phases.extraction.status == PhaseStatus.succeeded
    assert report.phases.source_logging.status == PhaseStatus.succeeded
    assert report.phases.mapping.status == PhaseStatus.succeeded
    assert report.phases.validation.status == PhaseStatus.pending
    assert report.phases.reporting.status == PhaseStatus.succeeded
    assert report.facts_extracted_total == 2

    source_states = {state.source: state for state in report.phases.extraction.sources}
    assert source_states[SupportedSource.linkedin].status == PhaseStatus.succeeded
    assert source_states[SupportedSource.skool].status == PhaseStatus.succeeded
    assert "enrich-test-run" in str(source_states[SupportedSource.linkedin].snapshot_path)
    assert "enrich-test-run" in str(source_states[SupportedSource.skool].snapshot_path)
    assert source_states[SupportedSource.linkedin].source_entity_ref is not None
    assert source_states[SupportedSource.linkedin].source_entity_path is not None
    assert source_states[SupportedSource.linkedin].facts_artifact_path is not None
    assert source_states[SupportedSource.skool].source_entity_ref is not None
    assert source_states[SupportedSource.skool].source_entity_path is not None
    assert source_states[SupportedSource.skool].facts_artifact_path is not None

    for source in (SupportedSource.linkedin, SupportedSource.skool):
        state = source_states[source]
        assert state.source_entity_path is not None
        assert state.facts_artifact_path is not None
        source_index_path = tmp_path / state.source_entity_path
        facts_artifact_path = tmp_path / state.facts_artifact_path
        assert source_index_path.exists()
        assert facts_artifact_path.exists()
        facts_payload = json.loads(facts_artifact_path.read_text(encoding="utf-8"))
        assert facts_payload["run_id"] == "enrich-test-run"
        assert facts_payload["source"] == source.value
        assert facts_payload["entity_slug"] == "founder-name"
        assert facts_payload["snapshot"]["path"] == state.snapshot_path
        assert facts_payload["facts"][0]["confidence"] == "medium"
        source_markdown = source_index_path.read_text(encoding="utf-8")
        assert "Structured facts artifact: [`facts.json`](facts.json)" in source_markdown
        assert "## Extracted Facts" in source_markdown

    report_path = tmp_path / config.run_report_path
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["phases"]["extraction"]["status"] == "succeeded"
    assert payload["phases"]["source_logging"]["status"] == "succeeded"
    assert payload["phases"]["mapping"]["status"] == "succeeded"
    assert payload["phases"]["validation"]["status"] == "pending"
    assert payload["phases"]["reporting"]["status"] == "succeeded"

    frontmatter, _ = _read_frontmatter_and_body(person_index_path)
    assert frontmatter["firm"] == "Legacy Labs"
    assert frontmatter["role"] in {"linkedin.com headline", "skool.com headline"}

    employment_rows = [
        json.loads(line)
        for line in (person_index_path.parent / "employment-history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(employment_rows) == 2
    assert employment_rows[-1]["organization"] == "Legacy Labs"
    assert employment_rows[-1]["role"] == "Founder"
    assert employment_rows[-1]["source_section"] == "employment_history_table"


def test_run_enrichment_for_entity_marks_failed_extraction_phase(tmp_path: Path) -> None:
    _write_person_fixture(tmp_path)
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _FailingAdapter(SupportedSource.linkedin, project_root=tmp_path),
            _SuccessfulAdapter(SupportedSource.skool, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "data/person/fo/person@founder-name/index.md",
        selected_sources=[SupportedSource.linkedin, SupportedSource.skool],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-test-failure",
    )

    assert report.status == RunStatus.failed
    assert report.phases.extraction.status == PhaseStatus.failed
    assert report.phases.source_logging.status == PhaseStatus.succeeded
    assert report.phases.mapping.status == PhaseStatus.skipped
    assert report.phases.validation.status == PhaseStatus.skipped
    assert report.phases.reporting.status == PhaseStatus.succeeded

    source_states = {state.source: state for state in report.phases.extraction.sources}
    assert source_states[SupportedSource.linkedin].status == PhaseStatus.failed
    assert source_states[SupportedSource.linkedin].error_type == "SourceAdapterError"
    assert source_states[SupportedSource.skool].status == PhaseStatus.succeeded
    assert source_states[SupportedSource.linkedin].source_entity_path is None
    assert source_states[SupportedSource.skool].source_entity_path is not None
    assert source_states[SupportedSource.skool].facts_artifact_path is not None

    skool_facts_path = tmp_path / str(source_states[SupportedSource.skool].facts_artifact_path)
    assert skool_facts_path.exists()


class _MixedConfidenceAdapter(_SuccessfulAdapter):
    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        return NormalizeResult(
            facts=[
                NormalizedFact(
                    attribute="headline",
                    value="Low confidence headline",
                    confidence=ConfidenceLevel.low,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value, "rank": "2"},
                ),
                NormalizedFact(
                    attribute="about",
                    value="High confidence summary",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value, "rank": "1"},
                ),
            ]
        )


def test_run_enrichment_for_entity_logs_all_confidence_levels(tmp_path: Path) -> None:
    person_index_path = _write_person_fixture(tmp_path)
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _MixedConfidenceAdapter(SupportedSource.linkedin, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "founder-name",
        selected_sources=[SupportedSource.linkedin],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-mixed-confidence-run",
    )

    source_state = report.phases.extraction.sources[0]
    assert source_state.status == PhaseStatus.succeeded
    assert report.phases.mapping.status == PhaseStatus.succeeded
    assert source_state.facts_artifact_path is not None
    facts_payload = json.loads((tmp_path / source_state.facts_artifact_path).read_text(encoding="utf-8"))
    assert [fact["confidence"] for fact in facts_payload["facts"]] == ["high", "low"]
    frontmatter, _ = _read_frontmatter_and_body(person_index_path)
    assert frontmatter["role"] == "Founder"


def test_run_enrichment_for_entity_never_prompts_after_kickoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_person_fixture(tmp_path)
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _SuccessfulAdapter(SupportedSource.linkedin, project_root=tmp_path),
        )
    )

    def _input_should_not_be_called(*_args, **_kwargs) -> str:
        raise AssertionError("interactive input was requested")

    monkeypatch.setattr("builtins.input", _input_should_not_be_called)

    report = run_enrichment_for_entity(
        "founder-name",
        selected_sources=[SupportedSource.linkedin],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-test-autonomous",
    )

    assert report.status == RunStatus.partial
    assert report.selected_sources == [SupportedSource.linkedin]


class _PersonFactsAdapter(_SuccessfulAdapter):
    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        return NormalizeResult(
            facts=[
                NormalizedFact(
                    attribute="current_company",
                    value="Future Labs",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
                NormalizedFact(
                    attribute="current_role",
                    value="Chief Executive Officer",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
                NormalizedFact(
                    attribute="location",
                    value="Low Confidence Location",
                    confidence=ConfidenceLevel.low,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
            ]
        )


def test_run_enrichment_for_entity_maps_person_with_confidence_gating(tmp_path: Path) -> None:
    person_index_path = _write_person_fixture(tmp_path)
    _, body_before = _read_frontmatter_and_body(person_index_path)
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _PersonFactsAdapter(SupportedSource.linkedin, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "founder-name",
        selected_sources=[SupportedSource.linkedin],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-person-mapping-run",
    )

    assert report.status == RunStatus.partial
    assert report.phases.mapping.status == PhaseStatus.succeeded

    frontmatter, body_after = _read_frontmatter_and_body(person_index_path)
    assert frontmatter["firm"] == "Future Labs"
    assert frontmatter["role"] == "Chief Executive Officer"
    assert frontmatter["location"] == "Kitchener, ON"
    assert body_after.strip() == body_before.strip()

    employment_rows = [
        json.loads(line)
        for line in (person_index_path.parent / "employment-history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(employment_rows) == 2
    assert employment_rows[-1]["organization"] == "Legacy Labs"
    assert employment_rows[-1]["role"] == "Founder"
    assert employment_rows[-1]["source_path"] == "data/person/fo/person@founder-name/index.md"


def _write_org_fixture(
    project_root: Path,
    *,
    slug: str = "future-labs",
    org_name: str = "Future Labs",
    website: str = "https://legacy.example.com",
    hq_location: str = "Toronto, ON",
    thesis: str = "Legacy thesis",
) -> Path:
    shard = shard_for_slug(slug)
    org_dir = project_root / "data" / "org" / shard / f"org@{slug}"
    org_dir.mkdir(parents=True, exist_ok=True)
    (org_dir / "edges").mkdir(parents=True, exist_ok=True)
    (org_dir / "edges" / ".gitkeep").write_text("", encoding="utf-8")
    (org_dir / "changelog.jsonl").write_text("", encoding="utf-8")
    index_path = org_dir / "index.md"
    index_path.write_text(
        "\n".join(
            [
                "---",
                f"org: {org_name}",
                "alias: null",
                f"website: {website}",
                f"hq-location: {hq_location}",
                "stages:",
                "- growth-stage",
                "check-size: null",
                f"thesis: {thesis}",
                "focus-sectors: []",
                "portfolio-examples: []",
                "created-at: 2026-02-20",
                "updated-at: 2026-02-20",
                "relationship-status: research",
                "known-people:",
                "- person: '[Founder Name](../../../person/fo/person@founder-name/index.md)'",
                "  relationship: former",
                "  relationship-details: Prior founder context.",
                "  relationship-start-date: null",
                "  relationship-end-date: null",
                "  first-noted-at: 2026-02-20",
                "  last-verified-at: 2026-02-20",
                "intro-paths: []",
                "last-updated-from-source: 2026-02-20",
                "---",
                "",
                "# Future Labs",
                "",
                "## Snapshot",
                "",
                "- Why this org matters: [Founder Name](../../../person/fo/person@founder-name/index.md) built this.",
                "",
                "## Bio",
                "",
                "Future Labs is referenced alongside [Founder Name](../../../person/fo/person@founder-name/index.md).",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return index_path


class _OrganizationFactsAdapter(_SuccessfulAdapter):
    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        return NormalizeResult(
            facts=[
                NormalizedFact(
                    attribute="organization_name",
                    value="Future Labs AI",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
                NormalizedFact(
                    attribute="website",
                    value="https://future-labs.ai",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
                NormalizedFact(
                    attribute="hq_location",
                    value="Waterloo, ON",
                    confidence=ConfidenceLevel.low,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
                NormalizedFact(
                    attribute="about",
                    value="Applied AI tooling for enterprise teams.",
                    confidence=ConfidenceLevel.medium,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                ),
                NormalizedFact(
                    attribute="known_person",
                    value="jane-founder",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={
                        "adapter": self.source.value,
                        "person_slug": "jane-founder",
                        "person_name": "Jane Founder",
                        "relationship": "current",
                        "relationship_details": "Co-founder and CEO.",
                    },
                ),
                NormalizedFact(
                    attribute="known_person",
                    value="missing-person",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={
                        "adapter": self.source.value,
                        "person_slug": "missing-person",
                        "person_name": "Missing Person",
                    },
                ),
            ]
        )


def test_run_enrichment_for_entity_maps_organization_with_confidence_gating(tmp_path: Path) -> None:
    _write_person_fixture(tmp_path, slug="founder-name")
    _write_person_fixture(tmp_path, slug="jane-founder", firm="Future Labs", role="Chief Executive Officer")
    org_index_path = _write_org_fixture(tmp_path)
    _, body_before = _read_frontmatter_and_body(org_index_path)

    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _OrganizationFactsAdapter(SupportedSource.linkedin, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "data/org/fu/org@future-labs/index.md",
        selected_sources=[SupportedSource.linkedin],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-org-mapping-run",
    )

    assert report.status == RunStatus.partial
    assert report.phases.mapping.status == PhaseStatus.succeeded

    frontmatter, body_after = _read_frontmatter_and_body(org_index_path)
    assert frontmatter["org"] == "Future Labs AI"
    assert frontmatter["website"] == "https://future-labs.ai"
    assert frontmatter["hq-location"] == "Toronto, ON"
    assert frontmatter["thesis"] == "Applied AI tooling for enterprise teams."
    assert body_after.strip() == body_before.strip()

    known_people = frontmatter["known-people"]
    assert isinstance(known_people, list)
    assert len(known_people) == 2

    jane_entry = next(entry for entry in known_people if "jane-founder" in str(entry.get("person")))
    assert jane_entry["person"] == "[Jane Founder](../../../person/ja/person@jane-founder/index.md)"
    assert jane_entry["relationship"] == "current"
    assert jane_entry["relationship-details"] == "Co-founder and CEO."
    assert "relationship-start-date" in jane_entry
    assert "relationship-end-date" in jane_entry
    assert "first-noted-at" in jane_entry
    assert "last-verified-at" in jane_entry
