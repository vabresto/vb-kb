from __future__ import annotations

import argparse
import json
from pathlib import Path

from kb.edges import derive_employment_edges, sync_edge_backlinks
from kb.generate_legacy_data import generate_legacy_data
from kb.mcp_server import run_server as run_fastmcp_server
from kb.migrate_notes_v2 import run_notes_migration
from kb.migrate_v2 import run_migration
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
        help="Data root directory (default: data-new if present, otherwise data).",
    )
    validate_parser.add_argument(
        "--changed",
        action="store_true",
        help="Validate only files changed relative to HEAD plus impacted references.",
    )
    validate_parser.add_argument("paths", nargs="*", help="Optional explicit paths to validate.")
    validate_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    migrate_parser = subparsers.add_parser("migrate-v2", help="Generate v2 folder layout")
    migrate_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    migrate_parser.add_argument(
        "--output-dir",
        default="data-new",
        help="Output directory (default: data-new).",
    )

    migrate_notes_parser = subparsers.add_parser(
        "migrate-notes-v2",
        help="Migrate legacy notes into v2 note@ folder layout.",
    )
    migrate_notes_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    migrate_notes_parser.add_argument(
        "--output-dir",
        default="data-new",
        help="Output directory (default: data-new).",
    )

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
        help="Data root directory (default: data-new if present, otherwise data).",
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
        help="Data root directory (default: data-new if present, otherwise data).",
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
        help="Data root directory (default: data-new if present, otherwise data).",
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

    legacy_data_parser = subparsers.add_parser(
        "build-legacy-data",
        help="Generate consolidated markdown files under data/ from data-new/ canonical records.",
    )
    legacy_data_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    legacy_data_parser.add_argument(
        "--source-root",
        default="data-new",
        help="Source root with canonical records (default: data-new).",
    )
    legacy_data_parser.add_argument(
        "--output-root",
        default="data",
        help="Output root for generated markdown (default: data).",
    )
    legacy_data_parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete output root before writing.",
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


def run_migrate(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    output = run_migration(project_root=project_root, output_dir=args.output_dir)
    run_notes_migration(project_root=project_root, output_dir=args.output_dir)
    print(output.relative_to(project_root).as_posix())
    return 0


def run_migrate_notes(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    output = run_notes_migration(project_root=project_root, output_dir=args.output_dir)
    print(output.relative_to(project_root).as_posix())
    return 0


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


def run_build_legacy_data(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    source_root = Path(args.source_root)
    if not source_root.is_absolute():
        source_root = (project_root / source_root).resolve()
    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = (project_root / output_root).resolve()

    result = generate_legacy_data(
        project_root=project_root,
        source_root=source_root,
        output_root=output_root,
        clean=not args.no_clean,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "validate":
        return run_validate(args)
    if args.command == "migrate-v2":
        return run_migrate(args)
    if args.command == "migrate-notes-v2":
        return run_migrate_notes(args)
    if args.command == "sync-edges":
        return run_sync_edges(args)
    if args.command == "derive-employment-edges":
        return run_derive_employment_edges(args)
    if args.command == "mcp-server":
        return run_mcp_server(args)
    if args.command == "build-legacy-data":
        return run_build_legacy_data(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
