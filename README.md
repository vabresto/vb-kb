# VB Knowledge Base

## Runtime status

- Canonical runtime is Python (`kb/` package, including `kb/mcp_server.py`).
- Legacy TypeScript/Cloudflare function code has been removed from this repo.
- Canonical source data lives under `data/`.

## Local setup

```bash
uv sync
```

To use semantic index/search commands, install the optional semantic extras:

```bash
uv sync --extra semantic
```

## Command discovery

```bash
just
```

The `justfile` in the repo root lists common workflows (validation, migrations, tests, site build/serve, and MCP server commands).

## Automation policy

- Any runnable workflow in this repo must be represented in the root `justfile`.
- When adding scripts, deploy commands, tests, or maintenance tasks, add or update a `just` target in the same change.

## Playwright enrichment layout

Python files for authenticated enrichment are organized by concern:

- `kb/enrichment_config.py`: typed enrichment config and env overrides (including per-source bootstrap commands).
- `kb/enrichment_adapters.py`: shared source-adapter contract and typed extraction/auth errors.
- `kb/enrichment_sessions.py`: session storageState read/write/import/export and missing/expired diagnostics.
- `kb/enrichment_bootstrap.py`: source bootstrap command runner for login/session creation with MFA/anti-bot challenge mapping.
- `kb/enrichment_playwright_bootstrap.py`: default Playwright bootstrap implementation used when no `KB_ENRICHMENT_*_BOOTSTRAP_COMMAND` override is set.
- `kb/enrichment_playwright_fetch.py`: default Playwright extraction implementation used when no `KB_ENRICHMENT_*_FETCH_COMMAND` override is set.
- `kb/enrichment_playwright_timing.py`: shared randomized wait settings/helpers used by Playwright bootstrap/fetch flows.
- `kb/enrichment_linkedin_adapter.py`: LinkedIn adapter implementation with session preflight/bootstrap fallback, fetch normalization, and snapshot persistence.
- `kb/enrichment_skool_adapter.py`: Skool adapter implementation with session preflight/bootstrap fallback, fetch normalization, and snapshot persistence.
- `kb/cli.py`: user-facing command wiring (`kb bootstrap-session`, `kb export-session`, `kb import-session`, `kb enrich-entity`).

Related tests:

- `kb/tests/test_enrichment_config.py`
- `kb/tests/test_enrichment_adapters.py`
- `kb/tests/test_enrichment_sessions.py`
- `kb/tests/test_enrichment_bootstrap.py`
- `kb/tests/test_enrichment_playwright_timing.py`
- `kb/tests/test_enrichment_playwright_bootstrap.py`
- `kb/tests/test_enrichment_playwright_fetch.py`
- `kb/tests/test_enrichment_linkedin_adapter.py`
- `kb/tests/test_enrichment_skool_adapter.py`
- `kb/tests/test_cli_bootstrap_session.py`
- `kb/tests/test_cli_enrich_entity.py`
- `kb/tests/test_cli_session_transfer.py`

Related runnable workflows:

- `just enrichment-bootstrap <source>`
- `just enrichment-bootstrap <source> "--headful"`
- `just enrichment-bootstrap <source> "--export-path <path>"`
- `just enrichment-bootstrap <source> "--bootstrap-command '<command>' --pretty"`
- `just enrichment-bootstrap <source> "<any kb bootstrap-session flags>" <project_root>`
- `just enrichment-bootstrap-headful <source> "--export-path <path>"` (convenience alias)
- `just enrichment-bootstrap <source> "--no-random-waits --pretty"`
- `just enrichment-session-export <source> <export_path>`
- `just enrichment-session-import <source> <import_path>`
- `just enrichment-run <entity-ref> "--source linkedin.com --source skool.com --pretty"`
- `just enrichment-run <entity-ref> "--source linkedin.com --headful --pretty"`
- `just enrichment-run <entity-ref> "--source linkedin.com --no-random-waits --pretty"`
- `just test-enrichment`
- `just linkedin-daemon headed=true`
- `just linkedin-daemon-client`
- `just linkedin-daemon-client http://127.0.0.1:8771 mode "human_control --actor human --reason inspect"`
- `just linkedin-daemon-client http://127.0.0.1:8771 mode "autonomous --actor human --reason resume"`
- `just linkedin-nyc-icp daemon_url="http://127.0.0.1:8771"`
- `just linkedin-search-plan theme_file="insurance_primary_icp_theme.txt" output="linkedin_people_search_plan.csv"`
- `just linkedin-collect-plan plan_csv="linkedin_people_search_plan.csv" output="linkedin_people_search_results_raw.csv" progress_log="linkedin_people_search_results_raw.progress.json" dedupe_mode="none"`
- `just linkedin-auth "<username>" "<password>" "<totp_secret_base32>"`
- `just linkedin-remote-start`
- `just linkedin-remote-status`
- `just linkedin-remote-stop`

### Shared LinkedIn daemon (human + agent)

- `scripts/linkedin_playwright_daemon.py` runs one long-lived Playwright browser with two contexts:
  - `automation_context`: the LinkedIn page the agent uses.
  - `control_context`: the `/control` tab/window for human mode + session save.
- The daemon serves:
  - control UI: `/control`
  - state API: `/api/state`
  - mode API: `/api/mode`
  - command API: `/api/command`
- Mode is persisted to `.build/enrichment/daemon/linkedin-daemon-state.json`.
- Supported modes:
  - `autonomous`: agent commands execute.
  - `human_control`: automation commands are blocked until resumed.
- Control page is auto-opened in a separate browser context and auto-reopened if closed.
- Agent commands always target `automation_context`; LinkedIn tabs opened from the control page/window are not automation targets.
- Control page includes **Save Session State JSON**, which writes current Playwright `storageState` back to the configured path (or an override path you provide in the input field).
- Use `scripts/linkedin_daemon_client.py` (or `just linkedin-daemon-client`) for CLI control:
  - `health`, `state`
  - `mode autonomous|human_control`
  - `cmd <daemon_cmd>`
  - `shutdown`

### Plan-driven high-volume people search collection

- Theme input:
  - `insurance_primary_icp_theme.txt` is a sample theme file.
- Generate deterministic query plan CSV:
  - `scripts/linkedin_generate_search_plan.py --theme-file <theme_file> --output <plan_csv>`
  - plan rows include canonical query params and canonical search URL (without `page`/`sid`).
- Execute full sweep from plan (search pages only):
  - `scripts/linkedin_collect_people_from_plan.py --plan-csv <plan_csv> --output <raw_csv> --progress-log <progress_json>`
  - each enabled query runs from page 1 until pagination ends (or per-row `max_pages`).
  - all scraped cards are appended by default (`--dedupe-mode none`).
  - optional global URL dedupe: `--dedupe-mode global`.
  - per-page commit/push and resumable progress are built in.

### Remote inspection (noVNC)

- `scripts/linkedin_remote_inspection.sh` manages:
  - `Xvfb` display for headed Chromium
  - `x11vnc` bridge
  - `websockify` + noVNC web endpoint
  - headed LinkedIn daemon
- Start:
  - `just linkedin-remote-start`
- Inspect remotely:
  - open the emitted noVNC URL (default `http://127.0.0.1:6081/vnc.html?...`)
  - open control page (default `http://127.0.0.1:8771/control`) to toggle mode.
- For remote hosts, tunnel the ports from your laptop:
  - `ssh -L 8771:127.0.0.1:8771 -L 6081:127.0.0.1:6081 <host>`

Recommended setup flow (repeatable):

1. Stop stale processes:
   - `just linkedin-remote-stop`
2. Start clean:
   - `just linkedin-remote-start open_control_tab=true`
3. Verify daemon health:
   - `just linkedin-daemon-client http://127.0.0.1:8771 health`
   - `just linkedin-daemon-client`
4. Open the noVNC URL and identify windows:
   - `LinkedIn Daemon Control - Chromium` is the control context.
   - Separate LinkedIn tab/window is the automation context (agent-controlled).
5. Confirm agent sees logged-in automation page:
   - `uv run python scripts/linkedin_daemon_client.py --daemon-url http://127.0.0.1:8771 cmd assert_authenticated`
6. Persist login/session cookies:
   - In `/control`, click **Save Session State JSON**.

Troubleshooting:

- Error: `Target page, context or browser has been closed`.
  - Cause: automation page/window was closed.
  - Fix: restart daemon stack (`just linkedin-remote-stop` then `just linkedin-remote-start`) and run `assert_authenticated` again.
- Error: `failed to start Xvfb` with `open_control_tab=false` in logs.
  - Cause: passed `open_control_tab=false` as a positional token to `scripts/linkedin_remote_inspection.sh`.
  - Fix: use either `just linkedin-remote-start open_control_tab=false` or script long flag `--open-control-tab false`.
- Control page shows LinkedIn logged in, but daemon state shows empty `automation_url`.
  - Cause: login happened in control context, not automation context.
  - Fix: switch to the automation window in noVNC and authenticate there, then save session state.

Bootstrap command contract:

- Bootstrap scripts should emit JSON as either raw Playwright `storageState` (`cookies` + `origins`) or `{ "storage_state": ... }`.
- If `KB_ENRICHMENT_*_BOOTSTRAP_COMMAND` is unset, default commands run `kb.enrichment_playwright_bootstrap` via `uv --with playwright`.
- If `KB_ENRICHMENT_*_FETCH_COMMAND` is unset, default commands run `kb.enrichment_playwright_fetch` via `uv --with playwright`.
- Default Playwright fetch scrolls profile pages before capture; LinkedIn extraction records `experience` facts and Skool extraction records scrolled `profile_entry` facts.
- If a slug-based direct profile URL does not resolve to a real profile page, default fetch retries with search-driven profile discovery (LinkedIn people search + fallback web search; Skool search + fallback web search) and then re-extracts from the best candidate profile URL.
- Playwright actions use randomized waits by default to reduce bot-like timing patterns.
  - Disable per command with `kb bootstrap-session --no-random-waits` or `kb enrich-entity --no-random-waits`.
  - Env override for command runners: `KB_ENRICHMENT_ACTION_RANDOM_WAITS=false`.
  - Optional wait range envs: `KB_ENRICHMENT_ACTION_RANDOM_WAIT_MIN_MS` and `KB_ENRICHMENT_ACTION_RANDOM_WAIT_MAX_MS`.
- Source logging deduplicates unchanged extraction output by reusing the latest matching source artifact for the same source/entity.

### Enrichment operation model (v1)

- Kickoff is always manual: run `just enrichment-run <entity-ref> ...` for exactly one typed entity ref/path target per invocation.
- Use typed entity refs (`person@<slug>`, `org@<slug>`, `source@<slug>`) instead of bare slugs.
- After kickoff, execution is autonomous (no interactive approval prompts): extraction, source logging, mapping, validation/remediation, and run reporting complete in one command.

### Local secret manager and env fallback

- Secret policy is configured through `KB_ENRICHMENT_SECRET_PROVIDER` (`local` default, `env` optional) and `KB_ENRICHMENT_SECRET_ENV_FALLBACK` (`true` default).
- Per-source secret references can be set with:
  - `KB_ENRICHMENT_LINKEDIN_USERNAME_SECRET` / `KB_ENRICHMENT_LINKEDIN_PASSWORD_SECRET`
  - `KB_ENRICHMENT_SKOOL_USERNAME_SECRET` / `KB_ENRICHMENT_SKOOL_PASSWORD_SECRET`
- Env fallback credentials are provided through the source credential env vars:
  - `KB_ENRICH_LINKEDIN_USERNAME` / `KB_ENRICH_LINKEDIN_PASSWORD`
  - `KB_ENRICH_SKOOL_USERNAME` / `KB_ENRICH_SKOOL_PASSWORD`
- LinkedIn TOTP can be provided via `KB_ENRICH_LINKEDIN_TOTP_SECRET` (base32 secret).
- Source env var names are also overrideable via:
  - `KB_ENRICHMENT_*_USERNAME_ENV`
  - `KB_ENRICHMENT_*_PASSWORD_ENV`
  - `KB_ENRICHMENT_LINKEDIN_TOTP_ENV` (and optional `KB_ENRICHMENT_SKOOL_TOTP_ENV`)

## Validate data

```bash
uv run kb validate --pretty
```

Validate only changed files:

```bash
uv run kb validate --changed --pretty
```

## Run a local view-only site

```bash
just site
```

MkDocs rebuilds from `data/` and serves at `http://127.0.0.1:8000`.

## Build static output

```bash
just site-build
```

Build output is written to `.build/site/`.

## Run auth integration tests (Keycloak external-jwt)

```bash
just test-auth-integration
```

This runs an isolated Docker Compose stack under `infra/deploy/auth-integration` and verifies that:

- a Keycloak confidential client can mint a bearer token
- `KB_MCP_OAUTH_MODE=external-jwt` validates that token at `/mcp`
- no container ports are published to the host (safe for concurrent runs)

To run the full test suite (unit + auth integration):

```bash
just test-all
```

## Semantic search

Build/update semantic vector index from markdown under `data/`:

```bash
just semantic-index
```

Query semantic index:

```bash
just semantic-search "founder profile for payments infra"
```

MCP clients can query the same index via the read-only tool:

- `semantic_search_data(query=..., limit=..., index_path=\".build/semantic/index.json\")`

Notes:

- Semantic index is derived and written to `.build/semantic/index.json` (not canonical data).
- Canonical truth remains markdown and structured files under `data/`.

## Run FastMCP write server

```bash
uv run kb mcp-server --transport stdio
```

For HTTP transport:

```bash
uv run kb mcp-server --transport streamable-http --host 127.0.0.1 --port 8001 --path /mcp
```

HTTP transports now enable a local in-memory OAuth provider by default (with state persisted at `.build/mcp-oauth-state.json`) so MCP clients that require OAuth discovery can connect cleanly.

Disable built-in OAuth for HTTP transport:

```bash
KB_MCP_OAUTH_MODE=off \
uv run kb mcp-server --transport streamable-http --host 127.0.0.1 --port 8001 --path /mcp
```

Optional OAuth overrides:

- `KB_MCP_OAUTH_BASE_URL` (for externally-reachable issuer/base URL)
- `KB_MCP_OAUTH_STATE_FILE` (for custom token-state file location)
- `KB_MCP_OAUTH_MODE=external-jwt` (validate external JWTs instead of issuing local OAuth tokens)

Use a shared local auth token when needed:

```bash
KB_MCP_AUTH_TOKEN=dev-secret \
uv run kb mcp-server --transport streamable-http --host 127.0.0.1 --port 8001 --path /mcp
```

## MCP auth model

- ChatGPT MCP supports OAuth2 Authorization Code + PKCE (S256) and does not support fixed API keys.
- The local HTTP MCP server includes a development OAuth provider (enabled by default for HTTP transports).
- Production ChatGPT integrations should still use PKCE OAuth in front of the Python MCP endpoint.
- The in-repo write server supports an optional shared token gate (`KB_MCP_AUTH_TOKEN`) for trusted/local flows.

## Transformation layer

Source files are canonical under `data/` for v2 records (`data/person/`, `data/org/`, and `data/source/`).

The site output is generated by:

- `kb/tools/build_site_content.py` (frontmatter/body transforms)
- `mkdocs_hooks.py` (runs transform before each MkDocs build/serve)

Edit `kb/tools/build_site_content.py` to change how frontmatter appears on final pages.

Site styles are in `kb/tools/site_assets/stylesheets/kb.css`.

## Deployment notes

- Docker + Traefik deployment guide (dev/prod/auth-integration envs) is in `DEPLOYMENT.md`.
- Cloudflare-specific notes are in `CLOUDFLARE_ACCESS.md`.
