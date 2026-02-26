#!/bin/sh
set -eu

server_url="http://${KEYCLOAK_INTERNAL_HOST:-keycloak}:8080"
realm="${KEYCLOAK_REALM:-vb-kb}"
client_id="${KEYCLOAK_CLIENT_ID:-vb-kb-mcp-confidential}"
client_secret="${KEYCLOAK_CLIENT_SECRET:-change-me-vb-kb-client-secret}"
client_name="${KEYCLOAK_CLIENT_NAME:-VB KB MCP Confidential}"
mapper_name="audience-${client_id}"

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
      -s 'redirectUris=["http://localhost/*","http://127.0.0.1/*"]' \
      -s 'webOrigins=["+"]' \
      -i | tr -d '\r'
  )"
else
  echo "[keycloak-init] updating client ${client_id}"
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
    -s 'redirectUris=["http://localhost/*","http://127.0.0.1/*"]' \
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
