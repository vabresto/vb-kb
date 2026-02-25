#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
ENV_FILE="${SCRIPT_DIR}/env"
PROJECT_NAME="vb-kb-auth-it-$(date +%s)-${RANDOM}"

compose() {
  docker compose \
    --project-name "${PROJECT_NAME}" \
    -f "${COMPOSE_FILE}" \
    --env-file "${ENV_FILE}" \
    "$@"
}

cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup EXIT

compose build kb-mcp auth-integration-tests
compose up --abort-on-container-exit --exit-code-from auth-integration-tests auth-integration-tests
