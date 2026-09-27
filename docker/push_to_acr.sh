#!/usr/bin/env bash
set -euo pipefail

ACR_REGISTRY="crpi-0vsre5argteykh9m.cn-guangzhou.personal.cr.aliyuncs.com"
ACR_NAMESPACE="zlx-personal"
IMAGE_NAME="media-resolver-api"
ACR_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RELEASE_TAG="$(git -C "${PROJECT_ROOT}" rev-parse --verify HEAD)"
DEPLOY_DIR="${MEDIARESOLVERAPI_DEPLOY_DIR:?MEDIARESOLVERAPI_DEPLOY_DIR must be set to the deploy directory on the target host}"

docker info >/dev/null
grep -q "${ACR_REGISTRY}" "${HOME}/.docker/config.json"
if [ -n "$(git -C "${PROJECT_ROOT}" status --porcelain)" ]; then
  echo "[ERROR] Refusing to publish a dirty worktree under ${RELEASE_TAG}" >&2
  exit 1
fi
[[ "${RELEASE_TAG}" =~ ^[0-9a-f]{40}$ ]]

# --provenance/--sbom 必须关闭：Docker 29 + buildx 默认附加 attestation，会产生
# application/vnd.oci.empty.v1+json descriptor，阿里云 ACR 个人版不支持该 manifest
# 类型，推送在所有 layer 上传完成后才失败（"unknown manifest class"）。
docker build \
  --provenance=false \
  --sbom=false \
  --build-arg "GIT_SHA=${RELEASE_TAG}" \
  -t "${IMAGE_NAME}:latest" \
  -f "${PROJECT_ROOT}/docker/Dockerfile" \
  "${PROJECT_ROOT}"

docker tag "${IMAGE_NAME}:latest" "${ACR_IMAGE}:${RELEASE_TAG}"
docker push "${ACR_IMAGE}:${RELEASE_TAG}"
echo "Published ${ACR_IMAGE}:${RELEASE_TAG} for ${DEPLOY_DIR}"