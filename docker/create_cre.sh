#!/usr/bin/env bash
# One-command CRE builder: profile YAML → docker build → validate → optional push → SQL.
#
# Quick start:
#   cp configs/cre_profile.example.yaml configs/cre_profile.yaml
#   # edit registry_url, image_repo_path, extras.cran / extras.conda_r
#   ./docker/create_cre.sh configs/cre_profile.yaml
#
# Push + print register SQL:
#   PUSH=1 ./docker/create_cre.sh configs/cre_profile.yaml
#
# Init a new profile from the example:
#   ./docker/create_cre.sh --init my_team
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN_DIR="${SCRIPT_DIR}/generated"
RENDER="${SCRIPT_DIR}/render_cre_profile.py"

usage() {
  cat <<'EOF'
Usage:
  ./docker/create_cre.sh configs/cre_profile.yaml
  PUSH=1 ./docker/create_cre.sh configs/cre_profile.yaml
  ./docker/create_cre.sh --init [name]     # copy example → configs/cre_profile.yaml

Environment (optional overrides):
  REGISTRY_URL, IMAGE_REPO_PATH, CRE_NAME, PUSH=1, SNOW_CLI, SNOW_CONNECTION

Requires: docker, python3 + PyYAML, snow CLI for validate (3.17+)
EOF
}

init_profile() {
  local name="${1:-cre_profile}"
  local dest="${REPO_ROOT}/configs/${name}.yaml"
  if [[ -f "${dest}" ]]; then
    echo "ERROR: ${dest} already exists"
    exit 1
  fi
  cp "${REPO_ROOT}/configs/cre_profile.example.yaml" "${dest}"
  echo "Created ${dest}"
  echo "Edit registry_url, image_repo_path, and extras.* then run:"
  echo "  ./docker/create_cre.sh ${dest}"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--init" ]]; then
  init_profile "${2:-cre_profile}"
  exit 0
fi

PROFILE="${1:-${REPO_ROOT}/configs/cre_profile.yaml}"
if [[ ! -f "${PROFILE}" ]]; then
  echo "ERROR: Profile not found: ${PROFILE}"
  echo "Run: ./docker/create_cre.sh --init"
  exit 1
fi

echo "==> Rendering CRE profile: ${PROFILE}"
python3 "${RENDER}" "${PROFILE}" -o "${GEN_DIR}"

# shellcheck source=/dev/null
source "${GEN_DIR}/cre_profile.env"

RUNTIME_CFG="${SFNB_RUNTIME_CONFIG:-cre_multilang_r.yaml}"
if [[ ! -f "${REPO_ROOT}/configs/${RUNTIME_CFG}" ]]; then
  echo "ERROR: runtime_config not found: configs/${RUNTIME_CFG}"
  exit 1
fi
cp "${REPO_ROOT}/configs/${RUNTIME_CFG}" "${GEN_DIR}/cre_runtime.yaml"
echo "==> Runtime config for image: configs/${RUNTIME_CFG} → docker/generated/cre_runtime.yaml"

export REGISTRY_URL IMAGE_REPO_PATH CRE_NAME IMAGE_TAG CRE_IMAGE_TAG SNOWBOOKS_TAG
export SFNB_CRE_ADBC SFNB_R_VERSION SFNB_RUNTIME_CONFIG SFNB_CONFIG_PATH
[[ -n "${SNOWFLAKER_TARBALL:-}" ]] && export SNOWFLAKER_TARBALL
[[ -n "${RSNOWFLAKE_TARBALL:-}" ]] && export RSNOWFLAKE_TARBALL

if [[ -z "${REGISTRY_URL:-}" || "${REGISTRY_URL}" == *"<account>"* ]]; then
  echo "ERROR: Set snowflake.registry_url in ${PROFILE} (or export REGISTRY_URL)"
  exit 1
fi

if [[ "${PUSH:-}" == "1" && -z "${IMAGE_REPO_PATH:-}" ]]; then
  echo "ERROR: Set snowflake.image_repo_path in ${PROFILE} when PUSH=1"
  exit 1
fi

if [[ "${PUSH:-}" == "1" ]]; then
  if [[ -n "${SNOW_CONNECTION:-}" ]]; then
    snow spcs image-registry login -c "${SNOW_CONNECTION}" || true
  else
    snow spcs image-registry login || true
  fi
fi

echo "==> Building image ${IMAGE_TAG} (profile ${CRE_IMAGE_TAG}, adbc=${SFNB_CRE_ADBC})"
PUSH="${PUSH:-0}" "${SCRIPT_DIR}/build_cre.sh"

echo ""
echo "==> Register in Snowflake (from profile):"
cat "${GEN_DIR}/cre_register.sql"
echo ""
echo "==> Workspace runtime: cre@${CRE_NAME}"
