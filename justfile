set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Show available tasks.
default:
  @just --list

# Install/update environment dependencies.
sync:
  uv sync

# Validate canonical KB data.
validate:
  uv run kb validate --pretty

# Validate changed scope only.
validate-changed:
  uv run kb validate --changed --pretty

# Run unit tests.
test:
  uv run --extra dev pytest -q

# Build transformed site_docs content.
build-site-content:
  uv run python tools/build_site_content.py

# Build static site output.
build-site:
  uv run mkdocs build

# Serve MkDocs locally.
serve-site:
  uv run mkdocs serve

# Run full local verification pass.
check:
  just validate-changed
  just test
  just build-site

# Migrate legacy person/org data into v2 layout.
migrate-v2:
  uv run kb migrate-v2 --output-dir data-new

# Migrate legacy notes into v2 note records.
migrate-notes:
  uv run kb migrate-notes-v2 --output-dir data-new

# Derive canonical employment edges from person JSONL rows.
derive-edges:
  uv run kb derive-employment-edges --data-root data-new

# Regenerate edge backlink symlinks.
sync-edges:
  uv run kb sync-edges --data-root data-new

# Build human-friendly consolidated markdown from data-new.
build-legacy-data:
  uv run kb build-legacy-data --source-root data-new --output-root data

# Run MCP server over stdio.
run-mcp-stdio:
  uv run kb mcp-server --transport stdio

# Run MCP server over streamable HTTP.
run-mcp-http host="127.0.0.1" port="8001" path="/mcp":
  uv run kb mcp-server --transport streamable-http --host {{host}} --port {{port}} --path {{path}}

# Run MCP server over streamable HTTP with token gate enabled.
run-mcp-http-auth token="dev-secret" host="127.0.0.1" port="8001" path="/mcp":
  KB_MCP_AUTH_TOKEN={{token}} uv run kb mcp-server --transport streamable-http --host {{host}} --port {{port}} --path {{path}}
