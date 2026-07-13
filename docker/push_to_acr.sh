#!/usr/bin/env bash
set -euo pipefail

ACR_REGISTRY="crpi-0vsre5argteykh9m.cn-guangzhou.personal.cr.aliyuncs.com"
ACR_NAMESPACE="zlx-personal"
IMAGE_NAME="media-resolver-api"
ACR_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RELEASE_TAG="$(git -C "${PROJECT_ROOT}" rev-parse --verify HEAD)"

docker info >/dev/null
grep -q "${ACR_REGISTRY}" "${HOME}/.docker/config.json"

docker build \
  --build-arg "GIT_SHA=${RELEASE_TAG}" \
  -t "${IMAGE_NAME}:latest" \
  -f "${PROJECT_ROOT}/docker/Dockerfile" \
  "${PROJECT_ROOT}"

docker tag "${IMAGE_NAME}:latest" "${ACR_IMAGE}:${RELEASE_TAG}"
docker tag "${IMAGE_NAME}:latest" "${ACR_IMAGE}:latest"
docker push "${ACR_IMAGE}:${RELEASE_TAG}"
docker push "${ACR_IMAGE}:latest"
echo "Published ${ACR_IMAGE}:${RELEASE_TAG}"
