from __future__ import annotations

import argparse
import json
from pathlib import Path

from kb.enrichment_adapters import AuthenticationError
from kb.enrichment_bootstrap import bootstrap_session_login
from kb.enrichment_config import SupportedSource, load_enrichment_config_from_env
from kb.enrichment_run import EnrichmentRunError, RunStatus, run_enrichment_for_entity
from kb.enrichment_sessions import export_session_state_json, import_session_state_json
from kb.edges import derive_citation_edges, derive_employment_edges, sync_edge_backlinks
from kb.mcp_server import run_server as run_fastmcp_server
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
            "Single target entity slug (e.g. founder-name) or canonical path "
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
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    enrich_entity_parser.add_argument(
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

    try:
        result = bootstrap_session_login(
            source,
            config=config,
            project_root=project_root,
            headless=headless,
            export_path=export_path,
            bootstrap_command=args.bootstrap_command,
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

    try:
        report = run_enrichment_for_entity(
            args.entity,
            selected_sources=args.sources,
            config=config,
            project_root=project_root,
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
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
