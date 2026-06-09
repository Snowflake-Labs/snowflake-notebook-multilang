#!/usr/bin/env bash
# Bake micromamba + workspace_env + snowflakeR/RSnowflake into a snowbooks CRE image.
# Run at Docker build time (linux/amd64) — not inside Workspace.
set -euo pipefail

ENV_NAME="${SFNB_ENV_NAME:-workspace_env}"
R_VERSION="${SFNB_R_VERSION:-4.5.2}"
MM_ROOT="${SFNB_MICROMAMBA_ROOT:-/home/jupyter/micromamba}"
CONDA_CHANNEL="${SFNB_CONDA_CHANNEL:-conda-forge}"
# ADBC: slow at notebook runtime (~2 min); bake by default (set SFNB_CRE_ADBC=0 to skip).
SFNB_CRE_ADBC="${SFNB_CRE_ADBC:-1}"
CRAN_MIRROR="${SFNB_CRAN_MIRROR:-https://cloud.r-project.org}"
CRE_IMAGE_TAG="${SFNB_CRE_IMAGE_TAG:-v1}"

# Match configs/cre_multilang_r.yaml (snowflakeR + RSnowflake preset).
CONDA_PACKAGES=(
  "r-base=${R_VERSION}"
  "r-cli"
  "r-rlang>=1.0.0"
  "r-reticulate>=1.25"
  "r-tidyverse"
  "r-dbplyr"
  "r-httr2>=1.0.0"
  "r-lazyeval"
  "r-dbi>=1.2.0"
  "r-jsonlite"
  "r-dplyr>=1.1.0"
)

# Arrow helpers used with ADBC / nanoarrow pipelines (small conda add-on).
ADBC_CONDA_PACKAGES=(
  "go=1.22.12"   # adbcsnowflake Makevars uses GOFLAGS=-modcacherw (removed in Go 1.26+)
  "libadbc-driver-snowflake"
  "r-nanoarrow"
)

SNOWFLAKER_TARBALL="${SNOWFLAKER_TARBALL:-https://github.com/Snowflake-Labs/snowflakeR/releases/download/v0.1.0/snowflakeR_0.1.0.tar.gz}"
RSNOWFLAKE_TARBALL="${RSNOWFLAKE_TARBALL:-https://github.com/Snowflake-Labs/RSnowflake/releases/download/v0.2.0/RSnowflake_0.2.0.tar.gz}"

echo "==> sfnb-multilang CRE pre-bake (${CRE_IMAGE_TAG}, env=${ENV_NAME}, R=${R_VERSION}, adbc=${SFNB_CRE_ADBC})"

mkdir -p "${MM_ROOT}/bin"
MM_BIN="${MM_ROOT}/bin/micromamba"

if [[ ! -x "${MM_BIN}" ]]; then
  echo "==> Downloading micromamba (linux-64)"
  curl -fSL --retry 3 --retry-delay 2 \
    -o /tmp/micromamba.tar.bz2 \
    "https://micro.mamba.pm/api/micromamba/linux-64/latest"
  tar -xjf /tmp/micromamba.tar.bz2 -C "${MM_ROOT}" bin/micromamba
  chmod +x "${MM_BIN}"
  rm -f /tmp/micromamba.tar.bz2
fi

export MAMBA_ROOT_PREFIX="${MM_ROOT}"
export PATH="${MM_ROOT}/bin:${PATH}"

echo "==> Creating conda env '${ENV_NAME}'"
"${MM_BIN}" create -y -n "${ENV_NAME}" -c "${CONDA_CHANNEL}" "${CONDA_PACKAGES[@]}"

ENV_PREFIX="$("${MM_BIN}" env list --json | ENV_NAME="${ENV_NAME}" python3 -c "
import json, os, sys
name = os.environ['ENV_NAME']
for p in json.load(sys.stdin).get('envs', []):
    if p.rstrip('/').endswith('/' + name):
        print(p)
        break
else:
    sys.exit('env not found: ' + name)
")"

echo "==> Environment prefix: ${ENV_PREFIX}"

# Helpers (r_helpers.py) read ~/.workspace_env_prefix
for home in /home/jupyter /root; do
  if [[ -d "${home}" ]]; then
    echo "${ENV_PREFIX}" > "${home}/.workspace_env_prefix"
    mkdir -p "${home}/micromamba" 2>/dev/null || true
    if [[ "${home}" != "/home/jupyter" ]] && [[ "${MM_ROOT}" == "/home/jupyter/micromamba" ]]; then
      ln -sfn /home/jupyter/micromamba "${home}/micromamba" 2>/dev/null || true
    fi
  fi
done

# Symlinks + timezone fixes (same as R plugin post_install)
LIB_DIR="${ENV_PREFIX}/lib"
for base in z lzma; do
  link="${LIB_DIR}/lib${base}.so"
  [[ -e "${link}" ]] && continue
  cand="$(ls "${LIB_DIR}"/lib${base}.so.* 2>/dev/null | head -1 || true)"
  [[ -n "${cand}" ]] && ln -sf "$(basename "${cand}")" "${link}" || true
done
mkdir -p /var/db/timezone
ln -sfn /usr/share/zoneinfo/UTC /var/db/timezone/localtime 2>/dev/null || true

echo "==> Installing snowflakeR + RSnowflake tarballs"
mkdir -p /tmp/sfnb-tarballs
curl -fSL -o /tmp/sfnb-tarballs/snowflakeR.tar.gz "${SNOWFLAKER_TARBALL}"
curl -fSL -o /tmp/sfnb-tarballs/RSnowflake.tar.gz "${RSNOWFLAKE_TARBALL}"

R_BIN="${ENV_PREFIX}/bin/R"
"${R_BIN}" --vanilla --quiet -e '
pkgs <- c("/tmp/sfnb-tarballs/snowflakeR.tar.gz", "/tmp/sfnb-tarballs/RSnowflake.tar.gz")
for (p in pkgs) {
  install.packages(p, repos = NULL, type = "source", quiet = TRUE)
}
for (n in c("snowflakeR", "RSnowflake")) {
  if (!requireNamespace(n, quietly = TRUE)) stop("missing package: ", n)
  message("OK: ", n, " ", as.character(packageVersion(n)))
}
'

if [[ "${SFNB_CRE_ADBC}" == "1" ]]; then
  echo "==> Installing ADBC conda deps into '${ENV_NAME}'"
  # r-base expects conda compilers when building R packages from source.
  # adbcsnowflake Go build requires CGO (_go_select=cgo, not nocgo).
  "${MM_BIN}" install -y -n "${ENV_NAME}" -c "${CONDA_CHANNEL}" \
    gcc_linux-64 gxx_linux-64 \
    "_go_select=2.3.0=cgo" \
    "${ADBC_CONDA_PACKAGES[@]}"

  GO_BIN="${ENV_PREFIX}/bin/go"
  MULTIVERSE="https://community.r-multiverse.org"
  export PATH="${ENV_PREFIX}/bin:${PATH}"
  export CONDA_PREFIX="${ENV_PREFIX}"
  export CONDA_DEFAULT_ENV="${ENV_NAME}"
  export MAMBA_ROOT_PREFIX="${MM_ROOT}"
  # Conda compiler wrappers (required when building ADBC R packages from source).
  if [[ -d "${ENV_PREFIX}/etc/conda/activate.d" ]]; then
    set +u
    for _act in "${ENV_PREFIX}/etc/conda/activate.d/"*.sh; do
      # shellcheck disable=SC1090
      [[ -f "${_act}" ]] && source "${_act}"
    done
    set -u
  fi

  echo "==> Installing R ADBC packages (adbcdrivermanager + adbcsnowflake)"
  # Prefer conda-forge binary if available; else compile with activated toolchain.
  if ! "${MM_BIN}" install -y -n "${ENV_NAME}" -c conda-forge r-adbcdrivermanager 2>/dev/null; then
    echo "WARN: r-adbcdrivermanager not on conda-forge — building from r-multiverse"
  fi

  "${R_BIN}" --vanilla -e "
Sys.setenv(GO_BIN = '${GO_BIN}')
options(pkg.sysreqs = FALSE)
mv <- '${MULTIVERSE}'
need <- c('adbcdrivermanager', 'adbcsnowflake')
need <- need[!vapply(need, function(p) requireNamespace(p, quietly=TRUE), logical(1))]
if (length(need)) install.packages(need, repos = mv, quiet = FALSE, Ncpus = 2L)
for (n in c('adbcdrivermanager', 'adbcsnowflake')) {
  if (!requireNamespace(n, quietly = TRUE)) stop('missing ADBC package: ', n)
  message('OK: ', n, ' ', as.character(packageVersion(n)))
}
" 2>&1 | tail -40
else
  echo "==> Skipping ADBC (SFNB_CRE_ADBC=${SFNB_CRE_ADBC})"
fi

# Notebook Python (snowbooks kernel) — rpy2 + sfnb-multilang toolkit + %%R auto-startup.
NOTEBOOK_PY=""
for PY in /opt/python/cpython-*/bin/python3 /usr/bin/python3; do
  [[ -x "${PY}" ]] && NOTEBOOK_PY="${PY}" && break
done

if [[ -n "${NOTEBOOK_PY}" ]]; then
  echo "==> pip install rpy2 + tabulate into ${NOTEBOOK_PY}"
  "${NOTEBOOK_PY}" -m pip install -q --break-system-packages 'rpy2>=3.5,<4' tabulate \
    || echo "WARN: pip rpy2/tabulate failed"

  echo "==> pip install sfnb-multilang (setup_notebook, enable_r_cells, …)"
  if [[ -f /tmp/sfnb-pkg/pyproject.toml ]]; then
    "${NOTEBOOK_PY}" -m pip install -q --break-system-packages /tmp/sfnb-pkg \
      || echo "WARN: pip install local sfnb-pkg failed"
  else
    SFNB_REF="${SFNB_MULTILANG_REF:-main}"
    curl -fSL -o /tmp/sfnb-multilang.zip \
      "https://github.com/Snowflake-Labs/snowflake-notebook-multilang/archive/refs/heads/${SFNB_REF}.zip"
    unzip -q -o /tmp/sfnb-multilang.zip -d /tmp
    "${NOTEBOOK_PY}" -m pip install -q --break-system-packages \
      "/tmp/snowflake-notebook-multilang-${SFNB_REF}" \
      || echo "WARN: pip install sfnb-multilang from GitHub failed"
    rm -rf /tmp/sfnb-multilang.zip "/tmp/snowflake-notebook-multilang-${SFNB_REF}"
  fi
else
  echo "WARN: notebook Python not found — skip rpy2/sfnb-multilang pip install"
fi

# Profile-driven extras (rendered by docker/create_cre.sh → docker/generated/cre_extra_install.sh)
if [[ -f /tmp/cre_extra_install.sh ]]; then
  echo "==> Running CRE profile extras"
  # shellcheck disable=SC1091
  source /tmp/cre_extra_install.sh
fi

# Config + IPython startup: %%R registers when the kernel starts (empty git repo OK).
SFNB_ROOT="/opt/sfnb"
mkdir -p "${SFNB_ROOT}/config"
CONFIG_PATH="${SFNB_CONFIG_PATH:-/opt/sfnb/config/cre_multilang_r.yaml}"
CONFIG_BASENAME="${CONFIG_PATH##*/}"
if [[ -f /tmp/cre_runtime.yaml ]]; then
  cp /tmp/cre_runtime.yaml "${SFNB_ROOT}/config/${CONFIG_BASENAME}"
elif [[ -f /tmp/cre_multilang_r.yaml ]]; then
  cp /tmp/cre_multilang_r.yaml "${SFNB_ROOT}/config/${CONFIG_BASENAME}"
elif [[ -f /tmp/sfnb-cre-assets/cre_multilang_r.yaml ]]; then
  cp /tmp/sfnb-cre-assets/cre_multilang_r.yaml "${SFNB_ROOT}/config/${CONFIG_BASENAME}"
fi

STARTUP_SRC=""
for cand in /tmp/00-sfnb-enable-r.py /tmp/sfnb-cre-assets/00-sfnb-enable-r.py; do
  [[ -f "${cand}" ]] && STARTUP_SRC="${cand}" && break
done

# Flat-file fallback helpers (also used if GitHub pip install fails).
for helper in r_helpers.py sfnb_setup.py; do
  if [[ -f "/tmp/${helper}" ]]; then
    mkdir -p "${SFNB_ROOT}/helpers"
    cp "/tmp/${helper}" "${SFNB_ROOT}/helpers/"
  fi
done

if [[ -n "${STARTUP_SRC}" ]]; then
  for home in /home/jupyter /root; do
    [[ -d "${home}" ]] || continue
    startup_dir="${home}/.ipython/profile_default/startup"
    mkdir -p "${startup_dir}"
    cp "${STARTUP_SRC}" "${startup_dir}/00-sfnb-enable-r.py"
    if id jupyter &>/dev/null; then
      chown -R jupyter:jupyter "${home}/.ipython" 2>/dev/null || true
    fi
  done
  echo "==> IPython startup installed (auto %%R on kernel start)"
fi

# Stamp image generation (visible in container for support/debug).
mkdir -p /opt/sfnb
echo "${CRE_IMAGE_TAG}" > /opt/sfnb/CRE_VERSION

echo "==> CRE ${CRE_IMAGE_TAG} pre-bake complete"
echo "    R ${R_VERSION} + snowflakeR + RSnowflake"
echo "    extras: rpy2, sfnb-multilang, auto %%R startup"
if [[ "${SFNB_CRE_ADBC}" == "1" ]]; then
  echo "    ADBC: adbcdrivermanager + adbcsnowflake (conda go + libadbc-driver-snowflake)"
fi
