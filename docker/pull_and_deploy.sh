#!/usr/bin/env bash
set -euo pipefail

ACR_REGISTRY="crpi-0vsre5argteykh9m.cn-guangzhou.personal.cr.aliyuncs.com"
ACR_NAMESPACE="zlx-personal"
IMAGE_NAME="media-resolver-api"
SERVICE_NAME="media-resolver"
ACR_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_NAME}"
: "${RELEASE_TAG:?RELEASE_TAG is required}"
if ! [[ "${RELEASE_TAG}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "[ERROR] RELEASE_TAG must be a full Git SHA" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.deploy.yml"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://localhost:8206/health}"
HEALTHCHECK_RETRIES="${HEALTHCHECK_RETRIES:-12}"
HEALTHCHECK_INTERVAL_SECONDS="${HEALTHCHECK_INTERVAL_SECONDS:-5}"

test -f "${COMPOSE_FILE}"
docker info >/dev/null

check_health() {
  curl -fsS --max-time 5 "${HEALTHCHECK_URL}" >/dev/null
}

snapshot_running_image() {
  local container_id image_id
  container_id="$(docker compose -f "${COMPOSE_FILE}" ps -q "${SERVICE_NAME}")"
  test -n "${container_id}"
  image_id="$(docker inspect --format '{{.Image}}' "${container_id}")"
  docker tag "${image_id}" "${IMAGE_NAME}:rollback"
}

rollback() {
  docker image inspect "${IMAGE_NAME}:rollback" >/dev/null
  docker tag "${IMAGE_NAME}:rollback" "${IMAGE_NAME}:latest"
  docker compose -f "${COMPOSE_FILE}" up -d
  for _ in $(seq 1 "${HEALTHCHECK_RETRIES}"); do
    check_health && return 0
    sleep "${HEALTHCHECK_INTERVAL_SECONDS}"
  done
  return 1
}

fail_with_rollback() {
  echo "[ERROR] $1; rolling back" >&2
  rollback && exit 2
  exit 1
}

snapshot_running_image || { echo "[ERROR] missing running rollback baseline" >&2; exit 1; }
docker pull "${ACR_IMAGE}:${RELEASE_TAG}"
docker tag "${ACR_IMAGE}:${RELEASE_TAG}" "${IMAGE_NAME}:latest"
docker compose -f "${COMPOSE_FILE}" up -d || fail_with_rollback "compose startup failed"

for _ in $(seq 1 "${HEALTHCHECK_RETRIES}"); do
  check_health && { echo "Release ${RELEASE_TAG} healthy"; exit 0; }
  sleep "${HEALTHCHECK_INTERVAL_SECONDS}"
done
fail_with_rollback "health check failed"