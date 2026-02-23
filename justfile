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

# Build transformed docs content under .build/docs.
build-site-content:
  uv run python tools/build_site_content.py

# Build static site output.
build-site:
  mkdir -p .build/docs
  uv run mkdocs build

# Serve MkDocs locally.
serve-site:
  mkdir -p .build/docs
  uv run mkdocs serve

# Run full local verification pass.
check:
  just validate-changed
  just test
  just build-site

# Derive canonical employment edges from person JSONL rows.
derive-edges:
  uv run kb derive-employment-edges --data-root data

# Regenerate edge backlink symlinks.
sync-edges:
  uv run kb sync-edges --data-root data

# Run MCP server over stdio.
run-mcp-stdio:
  uv run kb mcp-server --transport stdio

# Run MCP server over streamable HTTP.
run-mcp-http host="127.0.0.1" port="8001" path="/mcp":
  uv run kb mcp-server --transport streamable-http --host {{host}} --port {{port}} --path {{path}}

# Run MCP server over streamable HTTP with token gate enabled.
run-mcp-http-auth token="dev-secret" host="127.0.0.1" port="8001" path="/mcp":
  KB_MCP_AUTH_TOKEN={{token}} uv run kb mcp-server --transport streamable-http --host {{host}} --port {{port}} --path {{path}}
