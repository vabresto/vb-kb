from __future__ import annotations

import argparse
from pathlib import Path

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

    import json

    if args.pretty:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def run_migrate(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    output = run_migration(project_root=project_root, output_dir=args.output_dir)
    print(output.relative_to(project_root).as_posix())
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "validate":
        return run_validate(args)
    if args.command == "migrate-v2":
        return run_migrate(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
