# Changelog

All notable changes to the `sfnb-multilang` toolkit are documented here.

## [Unreleased]

### Added

- **ML CRE profile (`sfnb_ml.example.yaml`):** second image tag `ml-v1` /
  `cre@sfnb_multilang_ml` with tidymodels, xgboost, ranger, forecast, and
  foreach baked in; `configs/cre_multilang_ml.yaml` runtime preset for Posit /
  credit-risk E2E demos. Keeps lean `sfnb_multilang_r` v1 unchanged.
- **CRE `runtime_config`:** profile YAML selects which config file is copied
  into `/opt/sfnb/config/` at image build time.

### Fixed

- **CRE micromamba path:** `setup_notebook()` on a custom runtime no longer
  downloads micromamba to `/root/micromamba` when the image bakes R at
  `/home/jupyter/micromamba`. Honors `SFNB_MICROMAMBA_ROOT`, sets
  `MAMBA_ROOT_PREFIX` on micromamba subprocesses, and documents the path in
  `cre_multilang_r.yaml`.
- **`%%R` `-i` / `-o` comma lists:** `-o p, mtcars` (space after comma) no longer
  leaves an orphan token that rpy2 injects into the R cell (`mtcarslibrary(...)`).
  `SafeRMagics` normalizes IO lists to `-o p,mtcars` before parsing.

## [0.1.3] - 2026-04-28

### Fixed

- **Mirror URL authentication:** `urlunparse` was silently decoding
  percent-encoded characters (`%40` -> `@`) in credentials, breaking
  basic-auth URLs for Artifactory/Nexus. Replaced with manual string
  construction in `inject_auth_into_url` and `mask_url_credentials`
  (affects `installer.py` and `sfnb_setup.py`).
- **Secret path case sensitivity:** `_normalize_secret_path` now
  lowercases the path, matching Snowflake's case-insensitive object
  names. Previously `MYDB.MYSCHEMA.SECRET` would fail to resolve via
  the container mount at `/secrets/mydb/myschema/secret`.
- **urllib stripping inline credentials:** `urllib.request` ignores
  `user:pass@host` in URLs. Micromamba and tarball downloads now
  extract credentials and pass them via an `Authorization: Basic`
  header instead (affects `micromamba.py` and `sfnb_setup.py`).
- **CRAN auth missing:** `install.packages()` was called without
  authentication when `auth_secret` was configured. The R plugin now
  reads mirror credentials and passes a `Basic` auth header via R's
  `headers` parameter (R >= 4.1.0).
- **Tarball auth not injected:** R package tarball URLs from the
  `tarballs` config section were not receiving mirror credentials.
  `install_r_packages` now injects auth before download.
- **Network rule parsing:** `DESCRIBE NETWORK RULE` returns a
  `VALUE_LIST` column (not a property row) for `PRIVATE_HOST_PORT`
  rules. `_get_rule_domains` now checks for this column first before
  falling back to property-based parsing (affects `network_rules.py`
  and `sfnb_setup.py`).

### Added

- **`pypi_extra_index`** mirror config option: supports dual-index pip
  installs (`--index-url` + `--extra-index-url`). Useful when packages
  are split across a primary Artifactory PyPI repo and a secondary
  internal index.
- **Pip install verification:** after installing pip packages, each
  package is import-tested and the result logged (installed OK / import
  FAILED).

### Improved

- **CRAN install logging:** reports total/already-installed/missing
  package counts, post-install success/failure per package, and
  forwards R stderr to the Python logger.

### Removed

- Unused helper functions `_rpy2_available` and `_rscript_path` from
  `sfnb_setup.py`.

## [0.1.2] - 2026-04-10

- Initial public release with R, Scala/Java, and Julia support.
- Custom mirror configuration (Artifactory, Nexus).
- EAI auto-generation and domain management.
- `setup_notebook()` single-cell bootstrap.
