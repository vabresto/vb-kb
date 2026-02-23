#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import urllib.parse
from dataclasses import dataclass

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]]+\]:")
LINK_DEF_RE = re.compile(r"^\[[^\]]+\]:")


@dataclass(frozen=True)
class MentionPattern:
    text: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Entity:
    path: pathlib.Path
    mentions: tuple[MentionPattern, ...]


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    link_target: str | None
    line_number: int


def target_matches(relative_path: str) -> bool:
    if re.match(r"^data/(person|org)/[^/]+/(person|org)@[^/]+/index\.md$", relative_path):
        return True
    if re.match(r"^data/note/[^/]+/note@reflections-long-form-[^/]+/index\.md$", relative_path):
        return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ensure first mention of known people/orgs in canonical content files is "
            "linked to local KB entity files."
        )
    )
    parser.add_argument("files", nargs="*", help="Optional list of files to validate.")
    return parser.parse_args()


def parse_frontmatter(path: pathlib.Path) -> tuple[dict, str, int]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, 1

    raw_yaml = match.group(1)
    data = yaml.safe_load(raw_yaml) or {}
    body = text[match.end() :]
    body_start_line = text[: match.end()].count("\n") + 1
    return data, body, body_start_line


def normalize_aliases(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        alias = value.strip()
        return [alias] if alias else []
    if isinstance(value, list):
        aliases: list[str] = []
        for item in value:
            if isinstance(item, str):
                alias = item.strip()
                if alias:
                    aliases.append(alias)
        return aliases
    return []


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def build_mention_pattern(text: str) -> MentionPattern:
    escaped = re.escape(text)
    pattern = re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])")
    return MentionPattern(text=text, regex=pattern)


def load_entities() -> list[Entity]:
    entities: list[Entity] = []
    data_root = REPO_ROOT / "data"

    for kind in ("person", "org"):
        pattern = f"{kind}/**/{kind}@*/index.md"
        paths = sorted(data_root.glob(pattern))

        for path in paths:
            if path.name.startswith("_"):
                continue

            frontmatter, _, _ = parse_frontmatter(path)
            name_key = "person" if kind == "person" else "org"
            name = str(frontmatter.get(name_key, "")).strip()
            aliases = normalize_aliases(frontmatter.get("alias"))
            mention_values = unique_preserving_order([item for item in [name, *aliases] if item])
            if not mention_values:
                continue

            mentions = tuple(build_mention_pattern(value) for value in mention_values)
            entities.append(Entity(path=path.resolve(), mentions=mentions))

    return entities


def select_files(raw_files: list[str]) -> list[pathlib.Path]:
    if raw_files:
        candidates = [pathlib.Path(value) for value in raw_files]
    else:
        candidates = (
            sorted((REPO_ROOT / "data" / "person").rglob("index.md"))
            + sorted((REPO_ROOT / "data" / "org").rglob("index.md"))
            + sorted((REPO_ROOT / "data" / "note").rglob("note@reflections-long-form-*/index.md"))
        )

    selected: list[pathlib.Path] = []
    for candidate in candidates:
        if not candidate.is_absolute():
            candidate = (REPO_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()

        try:
            relative = candidate.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            continue

        if not candidate.exists():
            continue
        if candidate.name.startswith("_"):
            continue
        if target_matches(relative):
            selected.append(candidate)

    return selected


def strip_inline_code(line: str) -> str:
    return re.sub(r"`[^`]*`", "", line)


def iterate_tokens(body: str, start_line: int) -> list[Token]:
    tokens: list[Token] = []
    in_fence = False
    active_fence = ""

    for offset, line in enumerate(body.splitlines(), start=0):
        line_number = start_line + offset
        stripped = line.lstrip()

        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            if not in_fence:
                in_fence = True
                active_fence = fence
            elif fence == active_fence:
                in_fence = False
                active_fence = ""
            continue

        if in_fence:
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if FOOTNOTE_DEF_RE.match(stripped):
            continue
        if LINK_DEF_RE.match(stripped):
            continue

        clean = strip_inline_code(line)
        cursor = 0

        for match in LINK_RE.finditer(clean):
            if match.start() > cursor:
                text = clean[cursor : match.start()]
                if text.strip():
                    tokens.append(
                        Token(
                            kind="text",
                            text=text,
                            link_target=None,
                            line_number=line_number,
                        )
                    )

            label = match.group(1)
            destination = match.group(2)
            tokens.append(
                Token(
                    kind="link",
                    text=label,
                    link_target=destination,
                    line_number=line_number,
                )
            )
            cursor = match.end()

        trailing = clean[cursor:]
        if trailing.strip():
            tokens.append(
                Token(
                    kind="text",
                    text=trailing,
                    link_target=None,
                    line_number=line_number,
                )
            )

    return tokens


def first_mention_in_text(text: str, patterns: tuple[MentionPattern, ...]) -> str | None:
    best_start: int | None = None
    best_text: str | None = None
    for pattern in patterns:
        match = pattern.regex.search(text)
        if not match:
            continue

        match_start = match.start()
        if best_start is None or match_start < best_start:
            best_start = match_start
            best_text = pattern.text
            continue

        if match_start == best_start and best_text is not None:
            if len(pattern.text) > len(best_text):
                best_text = pattern.text

    return best_text


def normalize_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target:
        return None

    if target.startswith("<"):
        end = target.find(">")
        if end != -1:
            target = target[1:end].strip()

    target = target.split()[0]
    if not target:
        return None

    parsed = urllib.parse.urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if target.startswith("#"):
        return None
    return parsed.path


def link_targets_entity(
    source_file: pathlib.Path, link_target: str, entity_path: pathlib.Path
) -> bool:
    normalized_target = normalize_link_target(link_target)
    if not normalized_target:
        return False

    resolved_target = (source_file.parent / normalized_target).resolve()
    return resolved_target == entity_path.resolve()


def expected_link(source_file: pathlib.Path, entity_path: pathlib.Path) -> str:
    relative = os.path.relpath(entity_path, start=source_file.parent)
    return pathlib.PurePosixPath(relative).as_posix()


def check_file(path: pathlib.Path, entities: list[Entity]) -> list[str]:
    _, body, body_start_line = parse_frontmatter(path)
    tokens = iterate_tokens(body, body_start_line)
    file_errors: list[str] = []

    for entity in entities:
        if entity.path == path.resolve():
            continue

        for token in tokens:
            mention = first_mention_in_text(token.text, entity.mentions)
            if not mention:
                continue

            expected = expected_link(path, entity.path)
            location = f"{path.relative_to(REPO_ROOT).as_posix()}:{token.line_number}"

            if token.kind == "link":
                if token.link_target and link_targets_entity(
                    path, token.link_target, entity.path
                ):
                    break

                file_errors.append(
                    f"{location}: first mention of '{mention}' must link to '{expected}' "
                    f"(found '{token.link_target or ''}')."
                )
                break

            file_errors.append(
                f"{location}: first mention of '{mention}' must be linked as "
                f"[{mention}]({expected})."
            )
            break

    return file_errors


def main() -> int:
    args = parse_args()
    entities = load_entities()
    files = select_files(args.files)

    if not files:
        return 0

    errors: list[str] = []
    for file_path in files:
        errors.extend(check_file(file_path, entities))

    if errors:
        print("Entity link check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
