#!/bin/sh
set -eu

server_url="http://${KEYCLOAK_INTERNAL_HOST:-keycloak}:8080"
realm="${KEYCLOAK_REALM:-vb-kb}"
client_id="${KEYCLOAK_CLIENT_ID:-vb-kb-mcp-confidential}"
client_secret="${KEYCLOAK_CLIENT_SECRET:-change-me-vb-kb-client-secret}"
client_name="${KEYCLOAK_CLIENT_NAME:-VB KB MCP Confidential}"
chatgpt_redirect_uri="${KEYCLOAK_CHATGPT_REDIRECT_URI:-https://chatgpt.com/connector_platform_oauth_redirect}"
first_party_redirect_uri="${KEYCLOAK_FIRST_PARTY_REDIRECT_URI:-}"
kb_host="${VB_KB_HOST:-}"
mapper_name="audience-${client_id}"

if [ -z "${first_party_redirect_uri}" ] && [ -n "${kb_host}" ]; then
  first_party_redirect_uri="https://${kb_host}/*"
fi

redirect_uris_lines=""

append_unique_redirect_uri() {
  uri="$1"
  if [ -z "${uri}" ]; then
    return 0
  fi

  if [ -n "${redirect_uris_lines}" ] && printf '%s\n' "${redirect_uris_lines}" | grep -Fqx -- "${uri}"; then
    return 0
  fi

  if [ -z "${redirect_uris_lines}" ]; then
    redirect_uris_lines="${uri}"
  else
    redirect_uris_lines="${redirect_uris_lines}
${uri}"
  fi
}

ensure_required_redirect_uris() {
  append_unique_redirect_uri "http://localhost/*"
  append_unique_redirect_uri "http://127.0.0.1/*"
  append_unique_redirect_uri "${chatgpt_redirect_uri}"
  append_unique_redirect_uri "${first_party_redirect_uri}"

  if [ -n "${kb_host}" ]; then
    append_unique_redirect_uri "https://${kb_host}"
    append_unique_redirect_uri "https://${kb_host}/*"
  fi
}

load_existing_redirect_uris() {
  existing_redirect_payload="$(
    /opt/keycloak/bin/kcadm.sh get "clients/${client_uuid}" -r "${realm}" --fields redirectUris --format json \
      | tr -d '\r\n'
  )"

  redirect_uris_lines="$(
    printf '%s' "${existing_redirect_payload}" \
      | sed -n 's/.*"redirectUris"[[:space:]]*:[[:space:]]*\[\(.*\)\].*/\1/p' \
      | grep -o '"[^"]*"' \
      | sed 's/^"//;s/"$//' || true
  )"
}

build_redirect_uris_json() {
  json_items=""

  if [ -z "${redirect_uris_lines}" ]; then
    printf '[]'
    return 0
  fi

  while IFS= read -r uri; do
    if [ -z "${uri}" ]; then
      continue
    fi

    escaped_uri="$(printf '%s' "${uri}" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    if [ -z "${json_items}" ]; then
      json_items="\"${escaped_uri}\""
    else
      json_items="${json_items},\"${escaped_uri}\""
    fi
  done <<EOF
${redirect_uris_lines}
EOF

  printf '[%s]' "${json_items}"
}

echo "[keycloak-init] waiting for Keycloak admin API at ${server_url}"
until /opt/keycloak/bin/kcadm.sh config credentials \
  --server "${server_url}" \
  --realm master \
  --user "${KEYCLOAK_ADMIN}" \
  --password "${KEYCLOAK_ADMIN_PASSWORD}" >/dev/null 2>&1; do
  sleep 2
done

if ! /opt/keycloak/bin/kcadm.sh get "realms/${realm}" >/dev/null 2>&1; then
  echo "[keycloak-init] creating realm ${realm}"
  /opt/keycloak/bin/kcadm.sh create realms \
    -s "realm=${realm}" \
    -s "enabled=true" >/dev/null
fi

client_uuid="$(
  /opt/keycloak/bin/kcadm.sh get clients -r "${realm}" -q "clientId=${client_id}" \
    --fields id --format csv --noquotes \
    | grep -E '^[0-9a-f-]{36}$' | head -n1 || true
)"

if [ -z "${client_uuid}" ]; then
  echo "[keycloak-init] creating client ${client_id}"
  redirect_uris_lines=""
  ensure_required_redirect_uris
  redirect_uris_json="$(build_redirect_uris_json)"
  client_uuid="$(
    /opt/keycloak/bin/kcadm.sh create clients -r "${realm}" \
      -s "clientId=${client_id}" \
      -s "name=${client_name}" \
      -s "enabled=true" \
      -s "protocol=openid-connect" \
      -s "publicClient=false" \
      -s "clientAuthenticatorType=client-secret" \
      -s "secret=${client_secret}" \
      -s "standardFlowEnabled=true" \
      -s "directAccessGrantsEnabled=false" \
      -s "serviceAccountsEnabled=true" \
      -s "implicitFlowEnabled=false" \
      -s "fullScopeAllowed=true" \
      -s "redirectUris=${redirect_uris_json}" \
      -s 'webOrigins=["+"]' \
      -i | tr -d '\r'
  )"
else
  echo "[keycloak-init] updating client ${client_id}"
  load_existing_redirect_uris
  ensure_required_redirect_uris
  redirect_uris_json="$(build_redirect_uris_json)"
  /opt/keycloak/bin/kcadm.sh update "clients/${client_uuid}" -r "${realm}" \
    -s "name=${client_name}" \
    -s "enabled=true" \
    -s "protocol=openid-connect" \
    -s "publicClient=false" \
    -s "clientAuthenticatorType=client-secret" \
    -s "secret=${client_secret}" \
    -s "standardFlowEnabled=true" \
    -s "directAccessGrantsEnabled=false" \
    -s "serviceAccountsEnabled=true" \
    -s "implicitFlowEnabled=false" \
    -s "fullScopeAllowed=true" \
    -s "redirectUris=${redirect_uris_json}" \
    -s 'webOrigins=["+"]' >/dev/null
fi

mapper_uuid="$(
  /opt/keycloak/bin/kcadm.sh get "clients/${client_uuid}/protocol-mappers/models" -r "${realm}" \
    --fields id,name --format csv --noquotes \
    | grep ",${mapper_name}$" | head -n1 | cut -d, -f1 || true
)"

if [ -z "${mapper_uuid}" ]; then
  echo "[keycloak-init] creating audience mapper ${mapper_name}"
  /opt/keycloak/bin/kcadm.sh create "clients/${client_uuid}/protocol-mappers/models" -r "${realm}" \
    -s "name=${mapper_name}" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-audience-mapper" \
    -s "consentRequired=false" \
    -s "config.\"included.client.audience\"=${client_id}" \
    -s "config.\"access.token.claim\"=true" \
    -s "config.\"id.token.claim\"=false" >/dev/null
else
  echo "[keycloak-init] updating audience mapper ${mapper_name}"
  /opt/keycloak/bin/kcadm.sh update "clients/${client_uuid}/protocol-mappers/models/${mapper_uuid}" -r "${realm}" \
    -s "name=${mapper_name}" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-audience-mapper" \
    -s "consentRequired=false" \
    -s "config.\"included.client.audience\"=${client_id}" \
    -s "config.\"access.token.claim\"=true" \
    -s "config.\"id.token.claim\"=false" >/dev/null
fi

echo "[keycloak-init] bootstrap complete"
