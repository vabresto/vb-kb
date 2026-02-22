# Python MCP Deployment and Access Notes

Status: current as of 2026-02-22.

This repository is Python-first for MCP.

- Canonical MCP server: `kb/mcp_server.py`
- Canonical CLI entrypoint: `uv run kb mcp-server ...`
- TypeScript Cloudflare Pages Functions in `functions/` are deprecated and are not the supported runtime path.

## Deprecated path (do not use)

The following legacy Function routes are deprecated in this repository context:

- `GET /api/fetch`
- `GET /api/search`
- `POST /mcp` (TypeScript implementation)
- `GET /authorize`
- `GET /callback`
- `POST /token`
- `POST /register`
- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-authorization-server`

These were part of the deprecated TypeScript deployment model and are not authoritative for current docs.

## Supported Python MCP runtime

Run local stdio transport:

```bash
uv run kb mcp-server --transport stdio
```

Run HTTP transport:

```bash
uv run kb mcp-server --transport streamable-http --host 127.0.0.1 --port 8001 --path /mcp
```

Optional local/shared token gate:

```bash
KB_MCP_AUTH_TOKEN=dev-secret \
uv run kb mcp-server --transport streamable-http --host 127.0.0.1 --port 8001 --path /mcp
```

## ChatGPT MCP auth requirement

ChatGPT MCP clients support OAuth2 Authorization Code with PKCE (S256).
They do not support fixed API-key-only authentication.

For ChatGPT MCP deployments, use OAuth2 PKCE for the Python MCP server path.

## Recommended deployment shape

1. Run the Python FastMCP server (`kb mcp-server`) as the backend writer.
2. Publish only the Python MCP endpoint (for example `/mcp`) behind your edge/reverse proxy.
3. Terminate OAuth2 Authorization Code + PKCE at the auth layer used for ChatGPT MCP.
4. Forward only authenticated MCP traffic to the Python backend.

## Notes for this repository

- Existing migration, validation, and write transaction guarantees are implemented in Python.
- `openapi/custom-gpt-action.yaml` is retained only as a deprecated artifact and should not be used as the primary integration contract.
