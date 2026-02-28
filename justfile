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

# Run fast unit tests.
test:
  uv run --extra dev python -m pytest -q

# Run dockerized Keycloak external-jwt integration flow tests.
test-auth-integration:
  ./infra/deploy/auth-integration/run.sh

# Run all tests, including slower dockerized auth integration.
test-all:
  just test
  just test-auth-integration

# Build semantic vector index from canonical markdown data.
semantic-index model="BAAI/bge-small-en-v1.5" data_root="data" index_path=".build/semantic/index.json":
  uv run --extra semantic kb semantic-index --model {{model}} --data-root {{data_root}} --index-path {{index_path}}

# Query semantic vector index.
semantic-search query limit="8" index_path=".build/semantic/index.json":
  uv run --extra semantic kb semantic-search --query "{{query}}" --limit {{limit}} --index-path {{index_path}}

# Build static site output into .build/site.
site-build:
  mkdir -p .build/docs
  uv run mkdocs build

# Build and serve the site locally.
site:
  mkdir -p .build/docs
  uv run mkdocs serve

# Run full local verification pass.
check:
  just validate-changed
  just test
  just site-build

# Run Ralph autonomous loop from scripts/ralph.
ralph max_iterations="10":
  ./scripts/ralph/ralph.sh {{max_iterations}}

# Bootstrap source login and persist Playwright storageState session.
# Exposes full `kb bootstrap-session` flag surface via just.
enrichment-bootstrap source args="" project_root=".":
  uv run kb bootstrap-session "{{source}}" --project-root "{{project_root}}" {{args}}

# Convenience alias for local headful runs.
enrichment-bootstrap-headful source args="" project_root=".":
  uv run kb bootstrap-session "{{source}}" --project-root "{{project_root}}" --headful {{args}}

# Export an existing source session to transfer JSON.
enrichment-session-export source export_path project_root=".":
  uv run kb export-session "{{source}}" --project-root "{{project_root}}" --export-path "{{export_path}}"

# Import source session transfer JSON into canonical storageState location.
enrichment-session-import source import_path project_root=".":
  uv run kb import-session "{{source}}" --project-root "{{project_root}}" --import-path "{{import_path}}"

# Kick off one-entity enrichment run (autonomous execution after kickoff).
enrichment-run entity args="" project_root=".":
  uv run kb enrich-entity "{{entity}}" --project-root "{{project_root}}" {{args}}

# Run enrichment-focused tests (sessions/bootstrap/adapters/run/CLI).
test-enrichment:
  uv run --extra dev python -m pytest -q kb/tests/test_enrichment_*.py kb/tests/test_cli_bootstrap_session.py kb/tests/test_cli_enrich_entity.py kb/tests/test_cli_session_transfer.py

# Derive canonical employment edges from person JSONL rows.
derive-edges:
  uv run kb derive-employment-edges --data-root data

# Derive canonical citation edges from footnote references.
derive-citation-edges:
  uv run kb derive-citation-edges --data-root data

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

# Deploy bundled prod stack (Traefik + Keycloak + MCP + docs).
deploy-prod-up:
  if [ -f infra/deploy/prod/.env.local ]; then \
    docker compose --env-file infra/deploy/prod/.env --env-file infra/deploy/prod/.env.local -f infra/deploy/prod/docker-compose.yml up -d --build; \
  else \
    docker compose --env-file infra/deploy/prod/.env -f infra/deploy/prod/docker-compose.yml up -d --build; \
  fi

# Stop bundled prod stack.
deploy-prod-down:
  if [ -f infra/deploy/prod/.env.local ]; then \
    docker compose --env-file infra/deploy/prod/.env --env-file infra/deploy/prod/.env.local -f infra/deploy/prod/docker-compose.yml down --remove-orphans; \
  else \
    docker compose --env-file infra/deploy/prod/.env -f infra/deploy/prod/docker-compose.yml down --remove-orphans; \
  fi

# Show bundled prod stack status.
deploy-prod-ps:
  if [ -f infra/deploy/prod/.env.local ]; then \
    docker compose --env-file infra/deploy/prod/.env --env-file infra/deploy/prod/.env.local -f infra/deploy/prod/docker-compose.yml ps; \
  else \
    docker compose --env-file infra/deploy/prod/.env -f infra/deploy/prod/docker-compose.yml ps; \
  fi

# Re-run prod Keycloak runtime bootstrap (realm/client provisioning).
deploy-prod-keycloak-init:
  if [ -f infra/deploy/prod/.env.local ]; then \
    docker compose --env-file infra/deploy/prod/.env --env-file infra/deploy/prod/.env.local -f infra/deploy/prod/docker-compose.yml up --no-deps --force-recreate keycloak-init; \
  else \
    docker compose --env-file infra/deploy/prod/.env -f infra/deploy/prod/docker-compose.yml up --no-deps --force-recreate keycloak-init; \
  fi

# Tail bundled prod stack logs (optionally pass a service name).
deploy-prod-logs service="":
  if [ -f infra/deploy/prod/.env.local ]; then \
    if [ -n "{{service}}" ]; then \
      docker compose --env-file infra/deploy/prod/.env --env-file infra/deploy/prod/.env.local -f infra/deploy/prod/docker-compose.yml logs -f {{service}}; \
    else \
      docker compose --env-file infra/deploy/prod/.env --env-file infra/deploy/prod/.env.local -f infra/deploy/prod/docker-compose.yml logs -f; \
    fi; \
  else \
    if [ -n "{{service}}" ]; then \
      docker compose --env-file infra/deploy/prod/.env -f infra/deploy/prod/docker-compose.yml logs -f {{service}}; \
    else \
      docker compose --env-file infra/deploy/prod/.env -f infra/deploy/prod/docker-compose.yml logs -f; \
    fi; \
  fi
