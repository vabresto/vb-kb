from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any


def _http_request(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(
        url=url,
        method=method,
        data=data,
        headers=dict(headers or {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def _wait_for_status(
    url: str,
    *,
    deadline: float,
    statuses: set[int],
    name: str,
) -> tuple[int, Mapping[str, str], bytes]:
    last_status = None
    last_body = b""
    while time.time() < deadline:
        try:
            status, headers, body = _http_request("GET", url)
        except urllib.error.URLError:
            time.sleep(2.0)
            continue
        last_status = status
        last_body = body[:500]
        if status in statuses:
            return status, headers, body
        time.sleep(2.0)
    raise RuntimeError(
        f"timeout waiting for {name} at {url}; last_status={last_status} body={last_body.decode('utf-8', errors='replace')}"
    )


def _decode_jwt_payload(access_token: str) -> dict[str, Any]:
    parts = access_token.split(".")
    if len(parts) != 3:
        raise RuntimeError("access token is not a JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    return json.loads(decoded.decode("utf-8"))


def _fail(message: str) -> None:
    print(f"[auth-it] FAIL: {message}")
    raise RuntimeError(message)


def main() -> int:
    keycloak_base = (os.environ.get("KEYCLOAK_BASE_URL") or "").rstrip("/")
    mcp_base = (os.environ.get("MCP_BASE_URL") or "").rstrip("/")
    realm = (os.environ.get("KEYCLOAK_REALM") or "vb-kb").strip()
    client_id = (os.environ.get("KEYCLOAK_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("KEYCLOAK_CLIENT_SECRET") or "").strip()
    timeout_seconds = float(os.environ.get("TEST_TIMEOUT_SECONDS", "240"))

    if not keycloak_base or not mcp_base:
        _fail("KEYCLOAK_BASE_URL and MCP_BASE_URL are required")
    if not client_id or not client_secret:
        _fail("KEYCLOAK_CLIENT_ID and KEYCLOAK_CLIENT_SECRET are required")

    deadline = time.time() + timeout_seconds
    issuer = f"{keycloak_base}/realms/{realm}"
    metadata_url = f"{issuer}/.well-known/openid-configuration"
    resource_metadata_url = f"{mcp_base}/.well-known/oauth-protected-resource/mcp"
    mcp_url = f"{mcp_base}/mcp"

    print(f"[auth-it] waiting for Keycloak issuer metadata: {metadata_url}")
    _, _, openid_body = _wait_for_status(metadata_url, deadline=deadline, statuses={200}, name="keycloak")
    openid_config = json.loads(openid_body.decode("utf-8"))
    token_endpoint = str(openid_config.get("token_endpoint") or "").strip()
    if not token_endpoint:
        _fail("token_endpoint missing in Keycloak OIDC metadata")

    print(f"[auth-it] waiting for MCP protected resource metadata: {resource_metadata_url}")
    _, _, resource_body = _wait_for_status(
        resource_metadata_url,
        deadline=deadline,
        statuses={200},
        name="mcp protected resource metadata",
    )
    protected_resource = json.loads(resource_body.decode("utf-8"))
    authorization_servers = [str(value).rstrip("/") for value in protected_resource.get("authorization_servers", [])]
    if issuer.rstrip("/") not in authorization_servers:
        _fail(
            "MCP protected resource metadata does not advertise the expected authorization server "
            f"(expected={issuer}, got={authorization_servers})"
        )

    print("[auth-it] requesting client_credentials access token from Keycloak")
    token_payload = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    token_status, _, token_body = _http_request(
        "POST",
        token_endpoint,
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if token_status != 200:
        _fail(f"token request failed with status {token_status}: {token_body.decode('utf-8', errors='replace')}")

    token_response = json.loads(token_body.decode("utf-8"))
    access_token = str(token_response.get("access_token") or "")
    if not access_token:
        _fail("token response did not include access_token")

    claims = _decode_jwt_payload(access_token)
    if claims.get("azp") != client_id:
        _fail(f"unexpected azp claim: expected {client_id}, got {claims.get('azp')}")

    audience_claim = claims.get("aud")
    if isinstance(audience_claim, str):
        audiences = [audience_claim]
    elif isinstance(audience_claim, list):
        audiences = [str(value) for value in audience_claim]
    else:
        audiences = []
    if client_id not in audiences:
        _fail(f"token aud does not include client_id {client_id}; aud={audiences}")

    print("[auth-it] verifying MCP rejects missing and invalid bearer tokens")
    missing_status, _, _ = _http_request("GET", mcp_url)
    if missing_status != 401:
        _fail(f"expected 401 without bearer token, got {missing_status}")

    invalid_status, _, _ = _http_request(
        "GET",
        mcp_url,
        headers={"Authorization": "Bearer definitely-not-valid"},
    )
    if invalid_status != 401:
        _fail(f"expected 401 with invalid token, got {invalid_status}")

    print("[auth-it] verifying MCP accepts a Keycloak confidential-client bearer token")
    valid_status, _, valid_body = _http_request(
        "GET",
        mcp_url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if valid_status in {401, 403}:
        _fail(
            "expected authorized response class with valid token, "
            f"got {valid_status}: {valid_body.decode('utf-8', errors='replace')}"
        )

    print("[auth-it] PASS: external-jwt accepts Keycloak confidential client token")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[auth-it] ERROR: {exc}", file=sys.stderr)
        raise
