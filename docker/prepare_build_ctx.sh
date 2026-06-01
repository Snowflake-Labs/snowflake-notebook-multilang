#!/usr/bin/env bash
# Flatten docker/build-ctx for SPCS Image Builder (single-directory context).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CTX="${REPO_ROOT}/docker/build-ctx"

mkdir -p "${CTX}"

cp "${REPO_ROOT}/docker/build-ctx/Dockerfile" "${CTX}/Dockerfile"
cp "${REPO_ROOT}/docker/install_prebaked_r.sh" "${CTX}/install_prebaked_r.sh"
cp "${REPO_ROOT}/docker/generated/cre_extra_install.sh" "${CTX}/cre_extra_install.sh"
cp "${REPO_ROOT}/configs/cre_multilang_r.yaml" "${CTX}/cre_multilang_r.yaml"
cp "${REPO_ROOT}/docker/cre_ipython_startup/00-sfnb-enable-r.py" "${CTX}/00-sfnb-enable-r.py"
# Flat fallbacks if GitHub pip install fails during image build
cp "${REPO_ROOT}/notebooks/r_helpers.py" "${CTX}/r_helpers.py"
cp "${REPO_ROOT}/notebooks/sfnb_setup.py" "${CTX}/sfnb_setup.py"

echo "==> build-ctx ready: ${CTX}"
ls -la "${CTX}"
