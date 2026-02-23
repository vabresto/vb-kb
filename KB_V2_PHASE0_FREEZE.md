# KB v2 Phase 0 Freeze

Status: Accepted  
Date: 2026-02-22

This document freezes the Phase 0 inputs for KB v2 migration work.

## Naming and Canonical Layout

Entity directories:

- `person@<id>`
- `org@<id>`
- `source@<id>`
- `edge@<id>.json`

Canonical root:

- `data/` is the canonical v2 data root.

Sharding rule (v1):

- Use first two alphanumeric characters of slug (`alice-formwalt` -> `al`).
- Canonical entity paths:
  - `data/person/<shard>/person@<slug>/`
  - `data/org/<shard>/org@<slug>/`
  - `data/source/<shard>/source@<slug>/`

Required files per entity (v1):

- Person: `index.md`, `employment-history.jsonl`, `looking-for.jsonl`, `changelog.jsonl`, `edges/`
- Org: `index.md`, `changelog.jsonl`, `edges/`
- Note: `index.md`

## Note Frontmatter Schema Freeze (v1)

`index.md` frontmatter required keys:

- `id` (string, format: `source@<slug>`)
- `title` (string)
- `note-type` (snake_case string)
- `source-path` (string, path to legacy note source)

Optional keys:

- `date` (partial date: `YYYY` | `YYYY-MM` | `YYYY-MM-DD`)
- `source-category` (string)
- `updated-at` (partial date)

## JSONL Schema Freeze (v1)

`employment-history.jsonl` row shape:

- `id` (string)
- `period` (string)
- `organization` (string)
- `organization_ref` (nullable string, entity path)
- `role` (string)
- `notes` (nullable string)
- `source` (nullable string)
- `source_path` (string)
- `source_section` (string)
- `source_row` (nullable integer >= 1)

`looking-for.jsonl` row shape:

- `id` (string)
- `ask` (string)
- `details` (nullable string)
- `first_asked_at` (nullable partial date: `YYYY` | `YYYY-MM` | `YYYY-MM-DD`)
- `last_checked_at` (nullable partial date: `YYYY` | `YYYY-MM` | `YYYY-MM-DD`)
- `status` enum: `open`, `paused`, `closed`
- `notes` (nullable string)
- `source_path` (string)
- `source_section` (string)
- `source_row` (nullable integer >= 1)

`changelog.jsonl` row shape:

- `date` (partial date: `YYYY` | `YYYY-MM` | `YYYY-MM-DD`)
- `note` (string)

## Edge Relation Enum v1

Frozen enum values:

- `works_at`
- `founds`
- `co_founds`
- `invests_in`
- `advises`
- `introduces`
- `knows`
- `partners_with`
- `acquires`

Normalization rule:

- Legacy past-tense spellings (`worked_at`, `founded`, `co_founded`, `invested_in`, `introduced`, `partnered_with`, `acquired`) are accepted on read and normalized to the present-tense canonical enum during validation/write paths.

## Edge Required Fields (v1)

Each canonical edge JSON must include:

- `id`
- `relation`
- `directed`
- `from`
- `to`
- `first_noted_at`
- `last_verified_at`
- `valid_from`
- `valid_to`
- `sources`
- `notes`

Date fields use partial-date validation (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`).

## Migration Mapping Rules (Old -> New)

Person markdown:

- `data-old/person/<slug>.md` -> `data/person/<shard>/person@<slug>/index.md`

Org markdown:

- `data-old/org/<slug>.md` -> `data/org/<shard>/org@<slug>/index.md`

Sources markdown:

- `data-old/notes/<...>/<slug>.md` -> `data/source/<shard>/source@<normalized-path-slug>/index.md`

Entity links in markdown/frontmatter:

- Local links to `../person/<slug>.md` and `../org/<slug>.md` are rewritten to relative paths pointing to canonical v2 `index.md` targets.

Structured extraction rules:

- `## Employment History` table -> `employment-history.jsonl`
- Frontmatter `looking-for` (fallback to `## Looking For` table) -> `looking-for.jsonl`
- `## Changelog` bullet entries -> `changelog.jsonl`

Narrative preservation:

- `index.md` keeps narrative + identity summary and removes structured sections migrated to JSONL.

Generated full path map:

- `data/migration-path-map.csv`
