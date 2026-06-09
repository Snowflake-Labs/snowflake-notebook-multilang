#!/usr/bin/env bash
# Build sfnb-multilang CRE image inside Snowflake (SPCS Image Builder, preview).
# All account-specific values come from environment variables — set before running.
#
# Required:
#   SNOW_CONNECTION          Snowflake CLI connection name
#   SNOW_DATABASE            Database for image repository
#   SNOW_SCHEMA              Schema for image repository
#   SFNB_IMAGE_REPO          Fully qualified repo (e.g. MYDB.MYSCHEMA.MY_REPO)
#   SFNB_BUILD_POOL          Compute pool for builds
#   SFNB_BUILD_EAI           EAI with egress for build
#   REGISTRY_LOCAL_URL       e.g. myorg-myacct.registry-local.snowflakecomputing.com
#
# Optional:
#   SNOW_ROLE (default SYSADMIN), SFNB_IMAGE_NAME, SFNB_IMAGE_TAG, CRE_NAME
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export SNOWFLAKE_CLI_FEATURES_ENABLE_SPCS_BUILD_IMAGE="${SNOWFLAKE_CLI_FEATURES_ENABLE_SPCS_BUILD_IMAGE:-true}"

: "${SNOW_CONNECTION:?Set SNOW_CONNECTION}"
: "${SNOW_DATABASE:?Set SNOW_DATABASE}"
: "${SNOW_SCHEMA:?Set SNOW_SCHEMA}"
: "${SFNB_IMAGE_REPO:?Set SFNB_IMAGE_REPO (e.g. MYDB.MYSCHEMA.MY_REPO)}"
: "${SFNB_BUILD_POOL:?Set SFNB_BUILD_POOL}"
: "${SFNB_BUILD_EAI:?Set SFNB_BUILD_EAI}"
: "${REGISTRY_LOCAL_URL:?Set REGISTRY_LOCAL_URL for in-account FROM (registry-local host)}"

SNOW="${SNOW_CLI:-snow}"
ROLE="${SNOW_ROLE:-SYSADMIN}"
IMAGE_NAME="${SFNB_IMAGE_NAME:-sfnb-multilang-r}"
IMAGE_TAG="${SFNB_IMAGE_TAG:-v1}"
CRE_NAME="${CRE_NAME:-sfnb_multilang_r}"
BUILD_CTX="${REPO_ROOT}/docker/build-ctx"

echo "==> Preparing flat build context"
bash "${REPO_ROOT}/docker/prepare_build_ctx.sh"

echo "==> Server-side build (connection=${SNOW_CONNECTION}, pool=${SFNB_BUILD_POOL})"

if [[ "${1:-}" != "--skip-pool-check" ]]; then
  "${SNOW}" sql -c "${SNOW_CONNECTION}" -q \
    "ALTER COMPUTE POOL ${SFNB_BUILD_POOL} RESUME" 2>/dev/null || true
fi

"${SNOW}" spcs service build-image \
  --connection "${SNOW_CONNECTION}" \
  --role "${ROLE}" \
  --database "${SNOW_DATABASE}" \
  --schema "${SNOW_SCHEMA}" \
  --compute-pool "${SFNB_BUILD_POOL}" \
  --image-repository "${SFNB_IMAGE_REPO}" \
  --image-name "${IMAGE_NAME}" \
  --image-tag "${IMAGE_TAG}" \
  --build-context-dir "${BUILD_CTX}" \
  --eai-name "${SFNB_BUILD_EAI}" \
  --build-arg "REGISTRY_LOCAL_URL=${REGISTRY_LOCAL_URL}" \
  --verbose

REPO_PATH="$(echo "${SFNB_IMAGE_REPO}" | tr '[:upper:]' '[:lower:]' | tr '.' '/')"
echo ""
echo "==> Register CRE (adjust path if your repo naming differs):"
echo "CREATE OR REPLACE CUSTOM RUNTIME ENVIRONMENT ${CRE_NAME}"
echo "    IMAGE_PATH = '/${REPO_PATH}/${IMAGE_NAME}:${IMAGE_TAG}'"
echo "    BASE_IMAGE_TYPE = CPU;"
