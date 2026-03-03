from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

from kb.enrichment_adapters import AuthenticationError
from kb.enrichment_bootstrap import bootstrap_session_login
from kb.enrichment_config import EnrichmentConfig, SupportedSource, load_enrichment_config_from_env
from kb.enrichment_run import EnrichmentRunError, RunStatus, run_enrichment_for_entity
from kb.enrichment_sessions import export_session_state_json, import_session_state_json
from kb.edges import derive_citation_edges, derive_employment_edges, sync_edge_backlinks
from kb.mcp_server import EntityUpsertInput, upsert_entity_file, run_server as run_fastmcp_server
from kb.schemas import shard_for_slug
from kb.semantic import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHARS,
    DEFAULT_MODEL_CACHE_PATH,
    DEFAULT_MODEL_NAME,
    DEFAULT_OVERLAP_CHARS,
    FastEmbedBackend,
    build_semantic_index,
    load_semantic_index,
    resolve_runtime_path,
    search_semantic_index,
)
from kb.validate import run_validation, infer_data_root, collect_changed_paths, normalize_scope_paths

_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n?(?P<body>.*)\Z", re.DOTALL)
_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


class PersonInitError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KB v2 utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate KB v2 data")
    validate_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    validate_parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )
    validate_parser.add_argument(
        "--changed",
        action="store_true",
        help="Validate only files changed relative to HEAD plus impacted references.",
    )
    validate_parser.add_argument("paths", nargs="*", help="Optional explicit paths to validate.")
    validate_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    sync_edges_parser = subparsers.add_parser(
        "sync-edges",
        help="Regenerate endpoint edge symlinks from canonical edge files.",
    )
    sync_edges_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    sync_edges_parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )

    derive_edges_parser = subparsers.add_parser(
        "derive-employment-edges",
        help="Create canonical edges from person employment-history rows with organization_ref.",
    )
    derive_edges_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    derive_edges_parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )
    derive_edges_parser.add_argument(
        "--as-of",
        default=None,
        help="Date stamp for first_noted_at/last_verified_at (default: today, YYYY-MM-DD).",
    )
    derive_edges_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip running sync-edges after derivation.",
    )

    derive_citation_parser = subparsers.add_parser(
        "derive-citation-edges",
        help="Create canonical citation edges from footnote references to source records.",
    )
    derive_citation_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    derive_citation_parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )
    derive_citation_parser.add_argument(
        "--as-of",
        default=None,
        help="Date stamp for first_noted_at/last_verified_at (default: today, YYYY-MM-DD).",
    )
    derive_citation_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip running sync-edges after derivation.",
    )

    mcp_parser = subparsers.add_parser(
        "mcp-server",
        help="Run FastMCP write server for KB mutations.",
    )
    mcp_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    mcp_parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )
    mcp_parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http", "sse", "streamable-http"],
        help="FastMCP transport.",
    )
    mcp_parser.add_argument("--host", default="127.0.0.1", help="HTTP host for HTTP transports.")
    mcp_parser.add_argument("--port", type=int, default=8001, help="HTTP port for HTTP transports.")
    mcp_parser.add_argument("--path", default=None, help="Optional HTTP route path.")

    semantic_index_parser = subparsers.add_parser(
        "semantic-index",
        help="Build semantic index for markdown files under data root.",
    )
    semantic_index_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    semantic_index_parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )
    semantic_index_parser.add_argument(
        "--index-path",
        default=DEFAULT_INDEX_PATH,
        help=f"Index output path (default: {DEFAULT_INDEX_PATH}).",
    )
    semantic_index_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_NAME,
        help=f"Embedding model name (default: {DEFAULT_MODEL_NAME}).",
    )
    semantic_index_parser.add_argument(
        "--cache-dir",
        default=DEFAULT_MODEL_CACHE_PATH,
        help=f"Model cache directory (default: {DEFAULT_MODEL_CACHE_PATH}).",
    )
    semantic_index_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Maximum chunk size in characters (default: {DEFAULT_MAX_CHARS}).",
    )
    semantic_index_parser.add_argument(
        "--min-chars",
        type=int,
        default=DEFAULT_MIN_CHARS,
        help=f"Minimum chunk size in characters (default: {DEFAULT_MIN_CHARS}).",
    )
    semantic_index_parser.add_argument(
        "--overlap-chars",
        type=int,
        default=DEFAULT_OVERLAP_CHARS,
        help=f"Overlap for splitting large paragraphs (default: {DEFAULT_OVERLAP_CHARS}).",
    )

    semantic_search_parser = subparsers.add_parser(
        "semantic-search",
        help="Search an existing semantic index.",
    )
    semantic_search_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    semantic_search_parser.add_argument(
        "--index-path",
        default=DEFAULT_INDEX_PATH,
        help=f"Index path to search (default: {DEFAULT_INDEX_PATH}).",
    )
    semantic_search_parser.add_argument(
        "--query",
        required=True,
        help="Natural language search query.",
    )
    semantic_search_parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of results to return.",
    )
    semantic_search_parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Optional minimum cosine similarity score.",
    )
    semantic_search_parser.add_argument(
        "--model",
        default=None,
        help="Optional embedding model name (default: use model from index metadata).",
    )
    semantic_search_parser.add_argument(
        "--cache-dir",
        default=DEFAULT_MODEL_CACHE_PATH,
        help=f"Model cache directory (default: {DEFAULT_MODEL_CACHE_PATH}).",
    )

    bootstrap_session_parser = subparsers.add_parser(
        "bootstrap-session",
        help="Bootstrap and persist authenticated source session storageState.",
    )
    bootstrap_session_parser.add_argument(
        "source",
        choices=[source.value for source in SupportedSource],
        help="Supported source to bootstrap (linkedin.com or skool.com).",
    )
    bootstrap_session_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    bootstrap_session_parser.add_argument(
        "--headful",
        action="store_true",
        help="Run local non-headless bootstrap mode.",
    )
    bootstrap_session_parser.add_argument(
        "--export-path",
        type=Path,
        default=None,
        help=(
            "Optional export path for portable session JSON transfer payload. "
            "Defaults to a .build path when --headful is used."
        ),
    )
    bootstrap_session_parser.add_argument(
        "--bootstrap-command",
        default=None,
        help="Optional command override for source bootstrap login.",
    )
    bootstrap_session_parser.add_argument(
        "--no-random-waits",
        action="store_true",
        help="Disable randomized waits between browser actions.",
    )
    bootstrap_session_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    export_session_parser = subparsers.add_parser(
        "export-session",
        help="Export persisted source session storageState to a transfer JSON payload.",
    )
    export_session_parser.add_argument(
        "source",
        choices=[source.value for source in SupportedSource],
        help="Supported source to export (linkedin.com or skool.com).",
    )
    export_session_parser.add_argument(
        "--export-path",
        type=Path,
        required=True,
        help="Output path for exported transfer JSON payload.",
    )
    export_session_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    export_session_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    import_session_parser = subparsers.add_parser(
        "import-session",
        help="Import source session storageState from a transfer JSON payload.",
    )
    import_session_parser.add_argument(
        "source",
        choices=[source.value for source in SupportedSource],
        help="Supported source to import (linkedin.com or skool.com).",
    )
    import_session_parser.add_argument(
        "--import-path",
        type=Path,
        required=True,
        help="Input path for transfer JSON payload.",
    )
    import_session_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    import_session_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    enrich_entity_parser = subparsers.add_parser(
        "enrich-entity",
        help="Kick off a one-entity enrichment run with autonomous execution after kickoff.",
    )
    enrich_entity_parser.add_argument(
        "entity",
        help=(
            "Single target entity reference (e.g. person@founder-name) or canonical path "
            "(e.g. data/person/fo/person@founder-name/index.md)."
        ),
    )
    enrich_entity_parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=[source.value for source in SupportedSource],
        default=None,
        help=(
            "Source(s) to run. Repeat the flag to run multiple sources in one invocation. "
            "Defaults to all supported sources."
        ),
    )
    enrich_entity_parser.add_argument(
        "--headful",
        action="store_true",
        help=(
            "Run extraction in non-headless mode for this invocation by forcing "
            "all selected sources to headless_override=false."
        ),
    )
    enrich_entity_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    enrich_entity_parser.add_argument(
        "--no-random-waits",
        action="store_true",
        help="Disable randomized waits between browser actions during extraction.",
    )
    enrich_entity_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    person_init_parser = subparsers.add_parser(
        "person-init",
        help=(
            "Initialize a canonical person record from template and optionally run enrichment "
            "using provided LinkedIn/Skool profile URLs."
        ),
    )
    person_init_parser.add_argument(
        "--slug",
        default=None,
        help="Optional person slug override (lowercase kebab-case).",
    )
    person_init_parser.add_argument(
        "--name",
        default=None,
        help="Optional person display name override.",
    )
    person_init_parser.add_argument(
        "--linkedin-url",
        default=None,
        help="Optional LinkedIn profile URL override for enrichment bootstrap.",
    )
    person_init_parser.add_argument(
        "--skool-url",
        default=None,
        help="Optional Skool profile URL override for enrichment bootstrap.",
    )
    person_init_parser.add_argument(
        "--intro-note",
        default=None,
        help=(
            "Optional shared intro note. When provided, defaults both --how-we-met and --why-added "
            "unless those flags are set explicitly."
        ),
    )
    person_init_parser.add_argument(
        "--how-we-met",
        default=None,
        help="Optional note for how you met this person.",
    )
    person_init_parser.add_argument(
        "--why-added",
        default=None,
        help="Optional note for why this person was added to the KB.",
    )
    person_init_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    person_init_parser.add_argument(
        "--headful",
        action="store_true",
        help="Run extraction in non-headless mode when enrichment is triggered.",
    )
    person_init_parser.add_argument(
        "--no-random-waits",
        action="store_true",
        help="Disable randomized waits between browser actions during extraction.",
    )
    person_init_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    return parser


def run_validate(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)

    scope_paths = None
    scope_label = "full"
    if args.paths:
        scope_paths = normalize_scope_paths(project_root, args.paths)
        scope_label = "paths"
    elif args.changed:
        scope_paths = collect_changed_paths(project_root, data_root)
        scope_label = "changed"

    result = run_validation(
        project_root=project_root,
        data_root=data_root,
        scope_paths=scope_paths,
        scope_label=scope_label,
    )

    if args.pretty:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def run_sync_edges(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)
    result = sync_edge_backlinks(project_root=project_root, data_root=data_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def run_derive_employment_edges(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)

    derive_result = derive_employment_edges(
        project_root=project_root,
        data_root=data_root,
        as_of=args.as_of,
    )

    output: dict[str, object] = {"derive": derive_result}
    ok = derive_result["ok"]

    if not args.no_sync:
        sync_result = sync_edge_backlinks(project_root=project_root, data_root=data_root)
        output["sync"] = sync_result
        ok = ok and sync_result["ok"]

    print(json.dumps(output, sort_keys=True))
    return 0 if ok else 1


def run_derive_citation_edges(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)

    derive_result = derive_citation_edges(
        project_root=project_root,
        data_root=data_root,
        as_of=args.as_of,
    )

    output: dict[str, object] = {"derive": derive_result}
    ok = derive_result["ok"]

    if not args.no_sync:
        sync_result = sync_edge_backlinks(project_root=project_root, data_root=data_root)
        output["sync"] = sync_result
        ok = ok and sync_result["ok"]

    print(json.dumps(output, sort_keys=True))
    return 0 if ok else 1


def run_mcp_server(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)
    run_fastmcp_server(
        project_root=project_root,
        data_root=data_root,
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
    )
    return 0


def run_semantic_index(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)
    index_path = resolve_runtime_path(project_root, args.index_path)
    cache_dir = resolve_runtime_path(project_root, args.cache_dir)

    backend = FastEmbedBackend(
        model_name=args.model,
        cache_dir=cache_dir,
    )
    result = build_semantic_index(
        project_root=project_root,
        data_root=data_root,
        index_path=index_path,
        embedding_backend=backend,
        max_chars=args.max_chars,
        min_chars=args.min_chars,
        overlap_chars=args.overlap_chars,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def run_semantic_search(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    index_path = resolve_runtime_path(project_root, args.index_path)
    index_payload = load_semantic_index(index_path)

    model_payload = index_payload.get("model") or {}
    model_name = args.model or str(model_payload.get("name") or DEFAULT_MODEL_NAME)
    cache_dir = resolve_runtime_path(project_root, args.cache_dir)
    backend = FastEmbedBackend(
        model_name=model_name,
        cache_dir=cache_dir,
    )
    result = search_semantic_index(
        index_payload=index_payload,
        query=args.query,
        embedding_backend=backend,
        limit=args.limit,
        min_score=args.min_score,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def run_bootstrap_session(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    config = load_enrichment_config_from_env()
    source = SupportedSource(args.source)
    headless = not args.headful
    export_path = args.export_path
    if export_path is None and args.headful:
        export_path = Path(f".build/enrichment/sessions/{source.value}/headful-export.json")
    run_environ = None
    if args.no_random_waits:
        run_environ = dict(os.environ)
        run_environ["KB_ENRICHMENT_ACTION_RANDOM_WAITS"] = "false"
    bootstrap_kwargs: dict[str, object] = {}
    if run_environ is not None:
        bootstrap_kwargs["environ"] = run_environ

    try:
        result = bootstrap_session_login(
            source,
            config=config,
            project_root=project_root,
            headless=headless,
            export_path=export_path,
            bootstrap_command=args.bootstrap_command,
            **bootstrap_kwargs,
        )
    except AuthenticationError as exc:
        payload = {
            "ok": False,
            "source": exc.source,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "details": exc.details,
        }
        if args.pretty:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return 1

    payload = {
        "ok": True,
        **result.model_dump(mode="json"),
    }
    if args.pretty:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, sort_keys=True))
    return 0


def run_export_session(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    config = load_enrichment_config_from_env()
    source = SupportedSource(args.source)

    try:
        export_path = export_session_state_json(
            source,
            args.export_path,
            config=config,
            project_root=project_root,
        )
    except AuthenticationError as exc:
        payload = {
            "ok": False,
            "source": exc.source,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "details": exc.details,
        }
        if args.pretty:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return 1

    payload = {
        "ok": True,
        "source": source.value,
        "export_path": str(export_path),
    }
    if args.pretty:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, sort_keys=True))
    return 0


def run_import_session(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    config = load_enrichment_config_from_env()
    source = SupportedSource(args.source)

    try:
        session_state_path = import_session_state_json(
            source,
            args.import_path,
            config=config,
            project_root=project_root,
        )
    except AuthenticationError as exc:
        payload = {
            "ok": False,
            "source": exc.source,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "details": exc.details,
        }
        if args.pretty:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return 1

    payload = {
        "ok": True,
        "source": source.value,
        "session_state_path": str(session_state_path),
    }
    if args.pretty:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, sort_keys=True))
    return 0


def run_enrich_entity(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    config = load_enrichment_config_from_env()
    if args.headful:
        config = _force_headful_sources(config)
    run_environ = None
    if args.no_random_waits:
        run_environ = dict(os.environ)
        run_environ["KB_ENRICHMENT_ACTION_RANDOM_WAITS"] = "false"
    run_kwargs: dict[str, object] = {}
    if run_environ is not None:
        run_kwargs["environ"] = run_environ

    try:
        report = run_enrichment_for_entity(
            args.entity,
            selected_sources=args.sources,
            config=config,
            project_root=project_root,
            **run_kwargs,
        )
    except EnrichmentRunError as exc:
        payload = {
            "ok": False,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
        if args.pretty:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return 1

    payload = {
        "ok": report.status not in {RunStatus.failed, RunStatus.blocked},
        **report.model_dump(mode="json"),
    }
    if args.pretty:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, sort_keys=True))
    return 0 if report.status not in {RunStatus.failed, RunStatus.blocked} else 1


def run_person_init(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    try:
        requested_name = _normalize_optional_text(args.name)
        shared_intro_note = _normalize_optional_text(args.intro_note)
        how_we_met_note = _normalize_optional_text(args.how_we_met) or shared_intro_note
        why_added_note = _normalize_optional_text(args.why_added) or shared_intro_note
        linkedin_profile_url, linkedin_slug_hint = _normalize_linkedin_profile_url(args.linkedin_url)
        skool_profile_url, skool_slug_hint = _normalize_skool_profile_url(args.skool_url)
        person_slug = _resolve_person_slug(
            explicit_slug=args.slug,
            explicit_name=requested_name,
            linkedin_slug_hint=linkedin_slug_hint,
            skool_slug_hint=skool_slug_hint,
        )
        person_name = requested_name or _title_from_slug(person_slug)
        today = datetime.now(tz=UTC).date().isoformat()

        person_index_rel = f"data/person/{shard_for_slug(person_slug)}/person@{person_slug}/index.md"
        person_index_path = project_root / person_index_rel
        created = False
        if not person_index_path.exists():
            frontmatter, body = _render_person_template(
                project_root=project_root,
                person_name=person_name,
                today=today,
            )
            if how_we_met_note is not None:
                frontmatter["how-we-met"] = how_we_met_note
            if why_added_note is not None:
                frontmatter["why-added"] = why_added_note
            upsert_entity_file(
                project_root=project_root,
                data_root=project_root / "data",
                payload=EntityUpsertInput(
                    kind="person",
                    slug=person_slug,
                    frontmatter=frontmatter,
                    body=body,
                ),
            )
            created = True
        _ensure_person_record_support_files(person_index_path.parent)
        frontmatter_updates: dict[str, str] = {}
        if how_we_met_note is not None:
            frontmatter_updates["how-we-met"] = how_we_met_note
        if why_added_note is not None:
            frontmatter_updates["why-added"] = why_added_note
        frontmatter_fields_updated = _apply_person_frontmatter_updates(
            index_path=person_index_path,
            updates=frontmatter_updates,
        )

        source_url_overrides: dict[SupportedSource, str] = {}
        if linkedin_profile_url is not None:
            source_url_overrides[SupportedSource.linkedin] = linkedin_profile_url
        if skool_profile_url is not None:
            source_url_overrides[SupportedSource.skool] = skool_profile_url
        selected_sources = list(source_url_overrides.keys())

        report_payload: dict[str, object] | None = None
        status_code = 0
        if selected_sources:
            config = load_enrichment_config_from_env()
            if args.headful:
                config = _force_headful_sources(config)
            run_environ = None
            if args.no_random_waits:
                run_environ = dict(os.environ)
                run_environ["KB_ENRICHMENT_ACTION_RANDOM_WAITS"] = "false"
            run_kwargs: dict[str, object] = {}
            if run_environ is not None:
                run_kwargs["environ"] = run_environ
            report = run_enrichment_for_entity(
                f"person@{person_slug}",
                selected_sources=selected_sources,
                source_url_overrides=source_url_overrides,
                config=config,
                project_root=project_root,
                **run_kwargs,
            )
            report_payload = report.model_dump(mode="json")
            status_code = 0 if report.status not in {RunStatus.failed, RunStatus.blocked} else 1

        payload: dict[str, object] = {
            "ok": status_code == 0,
            "entity_ref": f"person@{person_slug}",
            "entity_slug": person_slug,
            "person_index_path": person_index_rel,
            "created": created,
            "selected_sources": [source.value for source in selected_sources],
            "frontmatter_fields_updated": frontmatter_fields_updated,
            "intro_notes": {
                "how-we-met": how_we_met_note,
                "why-added": why_added_note,
            },
            "source_url_overrides": {
                source.value: value
                for source, value in source_url_overrides.items()
            },
            "enrichment_triggered": bool(selected_sources),
        }
        if report_payload is not None:
            payload["enrichment_report"] = report_payload
        if args.pretty:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return status_code
    except (PersonInitError, EnrichmentRunError, ValueError, OSError, yaml.YAMLError) as exc:
        payload = {
            "ok": False,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
        if args.pretty:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, sort_keys=True))
        return 1


def _force_headful_sources(config: EnrichmentConfig) -> EnrichmentConfig:
    updated_sources = {
        source: settings.model_copy(update={"headless_override": False})
        for source, settings in config.sources.items()
    }
    return config.model_copy(update={"headless_default": False, "sources": updated_sources})


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _title_from_slug(slug: str) -> str:
    parts = [part for part in slug.split("-") if part]
    if not parts:
        return "Untitled"
    return " ".join(part.capitalize() for part in parts)


def _slugify_token(value: str) -> str:
    slug = _SLUG_SANITIZE_RE.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise PersonInitError("unable to derive slug from provided value")
    return slug


def _normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not slug:
        raise PersonInitError("slug must be non-empty")
    if _slugify_token(slug) != slug:
        raise PersonInitError("slug must be lowercase kebab-case")
    return slug


def _normalize_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if not candidate:
        raise PersonInitError("profile URL must be non-empty")
    parsed = urlparse(candidate)
    if not parsed.scheme:
        parsed = urlparse(f"https://{candidate}")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PersonInitError("profile URL must use http or https")
    return parsed.geturl()


def _normalize_linkedin_profile_url(raw_url: str | None) -> tuple[str | None, str | None]:
    normalized_input = _normalize_optional_text(raw_url)
    if normalized_input is None:
        return None, None
    normalized_url = _normalize_url(normalized_input)
    parsed = urlparse(normalized_url)
    host = parsed.netloc.lower()
    if "linkedin.com" not in host:
        raise PersonInitError("linkedin-url must point to linkedin.com")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2 or segments[0].lower() != "in":
        raise PersonInitError("linkedin-url must be a LinkedIn profile URL under /in/<slug>")
    profile_token = unquote(segments[1]).strip()
    if not profile_token:
        raise PersonInitError("linkedin-url profile slug is empty")
    person_slug_hint = _slugify_token(profile_token)
    return (f"https://www.linkedin.com/in/{profile_token}/", person_slug_hint)


def _normalize_skool_profile_url(raw_url: str | None) -> tuple[str | None, str | None]:
    normalized_input = _normalize_optional_text(raw_url)
    if normalized_input is None:
        return None, None
    normalized_url = _normalize_url(normalized_input)
    parsed = urlparse(normalized_url)
    host = parsed.netloc.lower()
    if "skool.com" not in host:
        raise PersonInitError("skool-url must point to skool.com")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments or not segments[0].startswith("@"):
        raise PersonInitError("skool-url must be a Skool profile URL under /@<handle>")
    handle = unquote(segments[0][1:]).strip()
    if not handle:
        raise PersonInitError("skool-url profile handle is empty")
    person_slug_hint = _slugify_token(handle)
    return (f"https://www.skool.com/@{handle}", person_slug_hint)


def _resolve_person_slug(
    *,
    explicit_slug: str | None,
    explicit_name: str | None,
    linkedin_slug_hint: str | None,
    skool_slug_hint: str | None,
) -> str:
    if explicit_slug is not None and explicit_slug.strip():
        return _normalize_slug(explicit_slug)
    if linkedin_slug_hint is not None:
        return linkedin_slug_hint
    if skool_slug_hint is not None:
        return skool_slug_hint
    if explicit_name is not None:
        return _slugify_token(explicit_name)
    raise PersonInitError("provide --slug, --name, --linkedin-url, or --skool-url")


def _load_person_template(*, project_root: Path) -> str:
    template_path = project_root / "data" / "person" / "_template" / "index.md"
    if not template_path.exists():
        raise PersonInitError(
            f"person template file not found at {template_path.relative_to(project_root).as_posix()}"
        )
    return template_path.read_text(encoding="utf-8")


def _render_person_template(*, project_root: Path, person_name: str, today: str) -> tuple[dict[str, object], str]:
    template = _load_person_template(project_root=project_root)
    rendered = template.replace("{{PERSON_NAME}}", person_name).replace("{{TODAY}}", today)
    match = _FRONTMATTER_BLOCK_RE.match(rendered)
    if match is None:
        raise PersonInitError("person template must include YAML frontmatter")
    frontmatter_raw = yaml.safe_load(match.group("frontmatter")) or {}
    if not isinstance(frontmatter_raw, dict):
        raise PersonInitError("person template frontmatter must be a mapping")
    body = (match.group("body") or "").strip()
    return dict(frontmatter_raw), body


def _read_markdown_document(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_BLOCK_RE.match(text)
    if match is None:
        raise PersonInitError(f"person index is missing YAML frontmatter: {path.as_posix()}")
    frontmatter_raw = yaml.safe_load(match.group("frontmatter")) or {}
    if not isinstance(frontmatter_raw, dict):
        raise PersonInitError(f"person index frontmatter must be a mapping: {path.as_posix()}")
    body = (match.group("body") or "").strip()
    return dict(frontmatter_raw), body


def _render_markdown_document(*, frontmatter: dict[str, object], body: str) -> str:
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).rstrip()
    rendered = f"---\n{dumped}\n---\n"
    body_text = body.strip()
    if body_text:
        rendered = f"{rendered}\n{body_text}\n"
    return rendered


def _apply_person_frontmatter_updates(
    *,
    index_path: Path,
    updates: dict[str, str],
) -> list[str]:
    if not updates:
        return []
    frontmatter, body = _read_markdown_document(index_path)
    updated_fields: list[str] = []
    for key, value in updates.items():
        current_raw = frontmatter.get(key)
        current_text = None if current_raw is None else _normalize_optional_text(str(current_raw))
        if current_text == value:
            continue
        frontmatter[key] = value
        updated_fields.append(key)
    if updated_fields:
        index_path.write_text(
            _render_markdown_document(frontmatter=frontmatter, body=body),
            encoding="utf-8",
        )
    return updated_fields


def _ensure_person_record_support_files(person_dir: Path) -> None:
    edges_dir = person_dir / "edges"
    edges_dir.mkdir(parents=True, exist_ok=True)
    gitkeep_path = edges_dir / ".gitkeep"
    if not gitkeep_path.exists():
        gitkeep_path.write_text("", encoding="utf-8")
    for file_name in ("changelog.jsonl", "employment-history.jsonl", "looking-for.jsonl"):
        path = person_dir / file_name
        if not path.exists():
            path.write_text("", encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "validate":
        return run_validate(args)
    if args.command == "sync-edges":
        return run_sync_edges(args)
    if args.command == "derive-employment-edges":
        return run_derive_employment_edges(args)
    if args.command == "derive-citation-edges":
        return run_derive_citation_edges(args)
    if args.command == "mcp-server":
        return run_mcp_server(args)
    if args.command == "semantic-index":
        return run_semantic_index(args)
    if args.command == "semantic-search":
        return run_semantic_search(args)
    if args.command == "bootstrap-session":
        return run_bootstrap_session(args)
    if args.command == "export-session":
        return run_export_session(args)
    if args.command == "import-session":
        return run_import_session(args)
    if args.command == "enrich-entity":
        return run_enrich_entity(args)
    if args.command == "person-init":
        return run_person_init(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
