#!/usr/bin/env bash
# Build, validate, tag, and optionally push the sfnb-multilang R CRE image.
#
# Prerequisites:
#   - Docker with buildx
#   - Snowflake CLI: snow spcs image-registry login
#
# Usage:
#   export REGISTRY_URL="<account>.registry.snowflakecomputing.com"
#   export IMAGE_REPO_PATH="/MYDB/MYSCHEMA/MY_REPO"   # logical repo path
#   ./docker/build_cre.sh
#
# Optional:
#   PUSH=1 ./docker/build_cre.sh
#   CRE_NAME=sfnb_multilang_r ./docker/build_cre.sh   # print CREATE CRE SQL after push

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN_DIR="${SCRIPT_DIR}/generated"

# Default runtime YAML for bare build_cre.sh (create_cre.sh overwrites this).
if [[ ! -f "${GEN_DIR}/cre_runtime.yaml" ]]; then
  mkdir -p "${GEN_DIR}"
  cp "${REPO_ROOT}/configs/cre_multilang_r.yaml" "${GEN_DIR}/cre_runtime.yaml"
fi
if [[ ! -f "${GEN_DIR}/cre_extra_install.sh" ]]; then
  mkdir -p "${GEN_DIR}"
  printf '%s\n' '# Auto-generated placeholder — run create_cre.sh for profile extras.' \
    'echo "  (no extra packages in profile)"' > "${GEN_DIR}/cre_extra_install.sh"
fi

IMAGE_TAG="${IMAGE_TAG:-sfnb-multilang-r:v1}"
SNOWBOOKS_TAG="${SNOWBOOKS_TAG:-2.5.0}"
PLATFORM="${PLATFORM:-linux/amd64}"

if [[ -z "${REGISTRY_URL:-}" ]]; then
  echo "ERROR: Set REGISTRY_URL to your Snowflake image registry host."
  echo "  Example: export REGISTRY_URL=myorg-myacct.registry.snowflakecomputing.com"
  echo "  Use lowercase repo path: mydb/myschema/my_repo (not MYDB/MYSCHEMA/...)"
  exit 1
fi

echo "==> Building ${IMAGE_TAG} (snowbooks:${SNOWBOOKS_TAG}, ${PLATFORM})"
CRE_IMAGE_TAG="${CRE_IMAGE_TAG:-v1}"
SFNB_CRE_ADBC="${SFNB_CRE_ADBC:-1}"
SFNB_CONFIG_PATH="${SFNB_CONFIG_PATH:-/opt/sfnb/config/cre_multilang_r.yaml}"
docker build --platform "${PLATFORM}" \
  --build-arg REGISTRY_URL="${REGISTRY_URL}" \
  --build-arg SNOWBOOKS_TAG="${SNOWBOOKS_TAG}" \
  --build-arg SFNB_CRE_IMAGE_TAG="${CRE_IMAGE_TAG}" \
  --build-arg SFNB_CRE_ADBC="${SFNB_CRE_ADBC}" \
  --build-arg SFNB_CONFIG_PATH="${SFNB_CONFIG_PATH}" \
  -f "${REPO_ROOT}/docker/Dockerfile.multilang-r" \
  -t "${IMAGE_TAG}" \
  "${REPO_ROOT}"

SNOW_VALIDATE="${SNOW_CLI:-snow}"
if "${SNOW_VALIDATE}" custom-image validate --help >/dev/null 2>&1; then
  echo "==> Validating image with Snowflake CLI"
  "${SNOW_VALIDATE}" custom-image validate "${IMAGE_TAG}" || {
    echo "WARNING: snow custom-image validate failed — fix issues before pushing."
    exit 1
  }
else
  echo "NOTE: snow custom-image validate requires CLI 3.17+ (skip or set SNOW_CLI)"
fi

if [[ "${PUSH:-}" != "1" ]]; then
  echo "==> Build complete. Set PUSH=1 and IMAGE_REPO_PATH to push to Snowflake."
  exit 0
fi

if [[ -z "${IMAGE_REPO_PATH:-}" ]]; then
  echo "ERROR: Set IMAGE_REPO_PATH (e.g. simon/scratch/my_repo) when PUSH=1"
  exit 1
fi

# IMAGE_REPO_PATH: lowercase db/schema/repo (Docker rejects uppercase), no leading slash
REPO_PATH="$(echo "${IMAGE_REPO_PATH#/}" | tr '[:upper:]' '[:lower:]')"
REMOTE="${REGISTRY_URL}/${REPO_PATH}/${IMAGE_TAG}"
echo "==> Tagging and pushing ${REMOTE}"
docker tag "${IMAGE_TAG}" "${REMOTE}"
docker push "${REMOTE}"

CRE_NAME="${CRE_NAME:-sfnb_multilang_r}"
echo ""
echo "==> Register the Custom Runtime Environment (run in Snowflake):"
echo "CREATE CUSTOM RUNTIME ENVIRONMENT ${CRE_NAME}"
echo "    IMAGE_PATH = '/${REPO_PATH}/${IMAGE_TAG}'"
echo "    BASE_IMAGE_TYPE = CPU;"
echo ""
echo "==> Use in Workspace advanced settings or:"
echo "EXECUTE NOTEBOOK PROJECT ... RUNTIME = 'cre@${CRE_NAME}' ...;"
