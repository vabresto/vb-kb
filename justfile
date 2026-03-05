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

# Scrape Waterloo alumni first-degree connections from LinkedIn search results.
linkedin-scrape-waterloo output="waterloo_first_degree_results.csv" max_pages="20" project_root="." surface="surface:2":
  ./scripts/linkedin_scrape/scrape_waterloo_first_degree.sh "{{output}}" "{{max_pages}}" "{{project_root}}" "{{surface}}"

# Scrape insurance operations 2nd-degree candidates with role heuristics.
linkedin-scrape-insurance-ops target="100" output="insurance_second_degree_icp_results.csv" max_pages="120" project_root="." surface="surface:2":
  ./scripts/linkedin_scrape/scrape_insurance_ops.sh "{{target}}" "{{output}}" "{{max_pages}}" "{{project_root}}" "{{surface}}"

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
# Exposes full `kb enrich-entity` flag surface via args (including `--headful`).
enrichment-run entity args="" project_root=".":
  uv run kb enrich-entity "{{entity}}" --project-root "{{project_root}}" {{args}}

# Start a long-running LinkedIn Playwright daemon HTTP server + control UI.
linkedin-daemon session_state=".build/enrichment/sessions/linkedin.com/storage-state.json" state_path=".build/enrichment/daemon/linkedin-daemon-state.json" host="127.0.0.1" port="8771" headed="false" open_control_tab="true":
  @cmd=(uv run --with playwright python scripts/linkedin_playwright_daemon.py --session-state "{{session_state}}" --state-path "{{state_path}}" --host "{{host}}" --port "{{port}}"); \
  if [ "{{headed}}" = "true" ]; then cmd+=(--headed); fi; \
  if [ "{{open_control_tab}}" != "true" ]; then cmd+=(--no-control-tab); fi; \
  "${cmd[@]}"

# Build NYC 2nd-degree insurance ICP list via daemon HTTP API.
linkedin-nyc-icp target_count="50" output="linkedin_nyc_insurance_icp_2nd_degree.csv" daemon_url="http://127.0.0.1:8771" max_pages_per_query="6" spawn_daemon="false" session_state=".build/enrichment/sessions/linkedin.com/storage-state.json" daemon_state_path=".build/enrichment/daemon/linkedin-daemon-state.json" headed="false" leave_daemon_running="false":
  @cmd=(uv run --with playwright python scripts/linkedin_nyc_icp_second_degree.py --target-count "{{target_count}}" --output "{{output}}" --daemon-url "{{daemon_url}}" --max-pages-per-query "{{max_pages_per_query}}"); \
  if [ "{{spawn_daemon}}" = "true" ]; then cmd+=(--spawn-daemon --session-state "{{session_state}}" --daemon-state-path "{{daemon_state_path}}"); fi; \
  if [ "{{headed}}" = "true" ]; then cmd+=(--headed); fi; \
  if [ "{{leave_daemon_running}}" = "true" ]; then cmd+=(--leave-daemon-running); fi; \
  "${cmd[@]}"

# Send control/inspection commands to running LinkedIn daemon.
linkedin-daemon-client daemon_url="http://127.0.0.1:8771" subcommand="state" args="":
  @cmd=(uv run python scripts/linkedin_daemon_client.py --daemon-url "{{daemon_url}}" {{subcommand}}); \
  if [ -n "{{args}}" ]; then cmd+=({{args}}); fi; \
  "${cmd[@]}"

# Start remote inspection stack (Xvfb + x11vnc + noVNC + headed daemon).
linkedin-remote-start display=":99" daemon_host="127.0.0.1" daemon_port="8771" vnc_port="5901" novnc_port="6081" session_state=".build/enrichment/sessions/linkedin.com/storage-state.json" daemon_state_path=".build/enrichment/daemon/linkedin-daemon-state.json" open_control_tab="true":
  ./scripts/linkedin_remote_inspection.sh start --display "{{display}}" --daemon-host "{{daemon_host}}" --daemon-port "{{daemon_port}}" --vnc-port "{{vnc_port}}" --novnc-port "{{novnc_port}}" --session-state "{{session_state}}" --daemon-state-path "{{daemon_state_path}}" --open-control-tab "{{open_control_tab}}"

# Stop remote inspection stack.
linkedin-remote-stop:
  ./scripts/linkedin_remote_inspection.sh stop

# Show remote inspection stack status.
linkedin-remote-status:
  ./scripts/linkedin_remote_inspection.sh status

# Authenticate LinkedIn with username/password + TOTP secret and persist storage state.
linkedin-auth username password totp_secret output_path=".build/enrichment/sessions/linkedin.com/storage-state.json" headed="false":
  @cmd=(uv run --with playwright python scripts/linkedin_auth_with_totp.py --username "{{username}}" --password "{{password}}" --totp-secret "{{totp_secret}}" --output-path "{{output_path}}"); \
  if [ "{{headed}}" = "true" ]; then cmd+=(--headed); fi; \
  "${cmd[@]}"

# Initialize a new person record from template and optionally bootstrap enrichment from profile URLs.
# Exposes `kb person-init` flags directly while keeping optional passthrough args.
person-init slug="" name="" linkedin_url="" skool_url="" intro_note="" how_we_met="" why_added="" headful="false" no_random_waits="false" pretty="false" args="" project_root=".":
  @cmd=(uv run kb person-init --project-root "{{project_root}}"); \
  if [ -n "{{slug}}" ]; then cmd+=(--slug "{{slug}}"); fi; \
  if [ -n "{{name}}" ]; then cmd+=(--name "{{name}}"); fi; \
  if [ -n "{{linkedin_url}}" ]; then cmd+=(--linkedin-url "{{linkedin_url}}"); fi; \
  if [ -n "{{skool_url}}" ]; then cmd+=(--skool-url "{{skool_url}}"); fi; \
  if [ -n "{{intro_note}}" ]; then cmd+=(--intro-note "{{intro_note}}"); fi; \
  if [ -n "{{how_we_met}}" ]; then cmd+=(--how-we-met "{{how_we_met}}"); fi; \
  if [ -n "{{why_added}}" ]; then cmd+=(--why-added "{{why_added}}"); fi; \
  if [ "{{headful}}" = "true" ]; then cmd+=(--headful); fi; \
  if [ "{{no_random_waits}}" = "true" ]; then cmd+=(--no-random-waits); fi; \
  if [ "{{pretty}}" = "true" ]; then cmd+=(--pretty); fi; \
  if [ -n "{{args}}" ]; then cmd+=({{args}}); fi; \
  "${cmd[@]}"

# Run enrichment-focused tests (sessions/bootstrap/adapters/run/CLI).
test-enrichment:
  uv run --extra dev python -m pytest -q kb/tests/test_enrichment_*.py kb/tests/test_cli_bootstrap_session.py kb/tests/test_cli_enrich_entity.py kb/tests/test_cli_person_init.py kb/tests/test_cli_session_transfer.py

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
