# Changelog

All notable changes to the `sfnb-multilang` toolkit are documented here.

## [Unreleased]

### Added

- **CRE profile builder:** `configs/cre_profile.example.yaml`, `docker/create_cre.sh`,
  and `docker/render_cre_profile.py` — declare extra conda/CRAN/pip packages in YAML,
  one command to render, build, validate, push, and print register SQL.
- **Docs:** CRE as organisation default — `docs/org_cre_operating_model.md`,
  `docs/cre_path_matrix.md`, `examples/cre_workspace_notebook_template.md`;
  removed account-specific examples from `docs/custom_runtime_images.md`.
- **`build_cre_snowflake.sh`:** requires env vars only (no baked-in connection/account names).
- **Custom Runtime Images (CRE):** `docker/Dockerfile.multilang-r` and
  `docker/build_cre.sh` to pre-bake micromamba, R 4.5.2, snowflakeR, and
  RSnowflake on the official `snowbooks` base image for Workspace Notebooks.
- **`configs/cre_multilang_r.yaml`:** preset config for notebooks using
  `cre@sfnb_multilang_r` (or your CRE name).
- **`docs/custom_runtime_images.md`:** end-to-end build, push, register, and
  attach workflow.
- **`setup_notebook()`:** detects pre-baked CRE images (`SFNB_CUSTOM_RUNTIME`
  or existing micromamba + R) and skips tarball reinstall when packages are
  already present; optional `custom_runtime.skip_eai_when_prebaked`.

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
