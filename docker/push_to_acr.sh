#!/usr/bin/env bash
set -euo pipefail

ACR_REGISTRY="crpi-0vsre5argteykh9m.cn-guangzhou.personal.cr.aliyuncs.com"
ACR_NAMESPACE="zlx-personal"
IMAGE_NAME="media-resolver-api"
ACR_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RELEASE_TAG="$(git -C "${PROJECT_ROOT}" rev-parse --verify HEAD)"
DEPLOY_TARGET="${DEPLOY_TARGET:-fordeal}"
DEPLOY_DIR="$(python3 - "${PROJECT_ROOT}/docker/deploy_targets.json" "${DEPLOY_TARGET}" <<'PY'
import json
import sys

with open(sys.argv[1]) as f:
    target = json.load(f)["targets"][sys.argv[2]]
print(target["dir"])
PY
)"

docker info >/dev/null
grep -q "${ACR_REGISTRY}" "${HOME}/.docker/config.json"
test -n "${DEPLOY_DIR}"
if [ -n "$(git -C "${PROJECT_ROOT}" status --porcelain)" ]; then
  echo "[ERROR] Refusing to publish a dirty worktree under ${RELEASE_TAG}" >&2
  exit 1
fi
[[ "${RELEASE_TAG}" =~ ^[0-9a-f]{40}$ ]]

docker build \
  --build-arg "GIT_SHA=${RELEASE_TAG}" \
  -t "${IMAGE_NAME}:latest" \
  -f "${PROJECT_ROOT}/docker/Dockerfile" \
  "${PROJECT_ROOT}"

docker tag "${IMAGE_NAME}:latest" "${ACR_IMAGE}:${RELEASE_TAG}"
docker push "${ACR_IMAGE}:${RELEASE_TAG}"
echo "Published ${ACR_IMAGE}:${RELEASE_TAG} for ${DEPLOY_TARGET}:${DEPLOY_DIR}"