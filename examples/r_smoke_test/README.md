# R Smoke Test -- Snowflake Workspace Notebook

Minimal validation that the **public** GitHub packages work end-to-end in a
Snowflake Workspace Notebook, without access to the private monorepo.

## What This Tests

| # | Area | What's validated |
|---|------|-----------------|
| 1 | R language execution | `%%R` magic, data.frame ops, vectorized math |
| 2 | snowflakeR connection | `sfr_connect()`, `sfr_query()` via Snowpark session |
| 3 | Model Registry | `sfr_model_registry()`, `sfr_show_models()` |
| 4 | Feature Store | `sfr_feature_store()`, `sfr_list_entities()`, `sfr_list_feature_views()` |
| 5 | RSnowflake DBI | `dbConnect(Snowflake())`, `dbGetQuery()`, `dbWriteTable()`, `dbReadTable()` |

## Files to Upload

Upload **all files in this folder** to a Snowflake Workspace Notebook:

| File | Purpose |
|------|---------|
| `sfnb_setup.py` | All-in-one bootstrap: EAI, R runtime, R packages, session context |
| `r_smoke_test_config.yaml` | Language, package, and (optional) session context / EAI config |
| `workspace_r_smoke_test.ipynb` | The test notebook |

## Setup

### Single-cell bootstrap

The notebook's first code cell calls `setup_notebook()`, which handles
everything automatically:

```python
from sfnb_setup import setup_notebook
setup_notebook(config="r_smoke_test_config.yaml", packages=["snowflakeR", "RSnowflake"])
```

This single call:

1. **Sets session context** -- reads `context:` from the YAML, or uses the
   session's existing database/schema/warehouse as defaults.
2. **Validates the EAI** -- discovers all attached EAIs via multi-tier
   introspection, checks required domains, and adds any that are missing
   (via `ALTER NETWORK RULE` on a managed EAI, or by creating a supplementary
   `MULTILANG_NOTEBOOK_EAI`).
3. **Installs the R runtime** -- pip-installs `sfnb-multilang`, then runs
   micromamba + conda-forge (~45 sec fresh, ~2 sec cached).
4. **Installs R packages** -- from tarball URLs, local `.tar.gz` files, or
   GitHub via `pak` (configured in the YAML).
5. **Exports SPCS OAuth env vars** -- so RSnowflake can authenticate via the
   built-in session token (no PAT needed).

### EAI (External Access Integration)

Workspace Notebooks block all outbound network traffic by default.
`setup_notebook()` automatically discovers and manages EAIs:

**How it works:**

- **EAI already attached?** Introspects its network rules, adds any missing
  domains via `ALTER NETWORK RULE`, and changes take effect immediately --
  no restart needed.
- **No EAI attached yet?** Creates one and prints instructions for the
  one-time manual attachment via the Snowsight UI.
- **No privileges?** Prints the complete SQL (with annotated domain
  coverage) for your admin.

Once created and attached, the same EAI can be reused across multiple
Workspace Notebooks -- it is a one-time setup per service.

**EAI name resolution (multi-tier):**

1. Explicit `eai.managed` name from the config YAML
2. `DESC SERVICE` (non-interactive/scheduled runs)
3. `.snowflake/settings.json` (best-effort hint)
4. `SHOW EXTERNAL ACCESS INTEGRATIONS` (all visible to role)
5. Fallback convention name: `MULTILANG_NOTEBOOK_EAI`

### First-time steps (no EAI yet)

1. Upload all files to a Workspace Notebook
2. (Optional) Edit `r_smoke_test_config.yaml` to set `context:` overrides
3. Open `workspace_r_smoke_test.ipynb`
4. **Run the setup cell** -- creates the EAI
5. Click **Connected** (top-left toolbar)
6. Hover over your service name and click **Edit**
7. Scroll to **External Access** > toggle **ON** the EAI > **Save**
8. Service restarts automatically
9. **Re-run the setup cell** (it updates the network rule), then run the tests

### Returning steps (EAI already attached)

1. Open the notebook
2. Run the setup cell (verifies/updates domains -- takes ~1 sec)
3. Run from the test sections onward

### Hosts in the EAI

The EAI allows outbound HTTPS to these hosts:

| Host | Purpose |
|------|---------|
| `micro.mamba.pm` | micromamba binary download |
| `api.anaconda.org` | Anaconda redirect target |
| `conda.anaconda.org` | conda-forge package index |
| `repo.anaconda.com` | conda-forge CDN |
| `binstar-cio-packages-prod.s3.amazonaws.com` | Anaconda S3 storage |
| `cloud.r-project.org` | CRAN package downloads |
| `bioconductor.org` | Bioconductor config (pak init) |
| `github.com` | GitHub repos (sfnb-multilang, snowflakeR, RSnowflake) |
| `api.github.com` | GitHub API (pak dependency resolution) |
| `codeload.github.com` | GitHub source archives |
| `objects.githubusercontent.com` | GitHub raw content |
| `release-assets.githubusercontent.com` | GitHub release asset downloads |
| `pypi.org` | PyPI index (sfnb-multilang, rpy2) |
| `files.pythonhosted.org` | PyPI file downloads |

## Prerequisites

### RSnowflake Authentication

RSnowflake uses the Snowflake SQL API v2. In Workspace Notebooks the built-in
SPCS OAuth token (`/snowflake/session/token`) is used automatically -- no
Programmatic Access Token (PAT) is required. `setup_notebook()` exports the
necessary environment variables (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, etc.)
so that `dbConnect(Snowflake())` works with zero configuration.

### R Package Installation Options

Three install methods are supported per package (auto-detected):

| Method | What you do | First run | Cached |
|--------|-------------|-----------|--------|
| **URL download** (recommended) | Set release URL in `r_smoke_test_config.yaml` | ~10 sec | ~10 sec |
| **Local tarball** | Upload `.tar.gz` files to Workspace | ~10 sec | ~10 sec |
| **GitHub via pak** (fallback) | Nothing -- downloads automatically | ~2 min | ~10 sec |

The tarball paths are ~12x faster on a clean container (no `pak` bootstrap,
no bioconductor.org dependency).

**Configuration** (`r_smoke_test_config.yaml`):

```yaml
languages:
  r:
    enabled: true
    tarballs:
      # URL -- downloaded at install time (needs EAI access to host)
      snowflakeR: "https://github.com/Snowflake-Labs/snowflakeR/releases/download/v0.1.0/snowflakeR_0.1.0.tar.gz"
      RSnowflake: "https://github.com/Snowflake-Labs/RSnowflake/releases/download/v0.2.0/RSnowflake_0.2.0.tar.gz"
      # Local path -- installed directly
      # myPackage: "libs/myPackage_1.0.0.tar.gz"
      # Omitted -- searches Workspace recursively, then falls back to pak
```

If `tarballs` is omitted entirely, the notebook searches the Workspace
(including subfolders) for `snowflakeR_*.tar.gz` and `RSnowflake_*.tar.gz`.
If multiple versions are found, the newest is installed.

Additional R packages beyond snowflakeR and RSnowflake can also be listed
under `tarballs` -- they are installed from their URL or local tarball.

**Where to get the tarballs:** GitHub Releases:

- <https://github.com/Snowflake-Labs/snowflakeR/releases>
- <https://github.com/Snowflake-Labs/RSnowflake/releases>

Or via CLI:

```bash
gh release download --repo Snowflake-Labs/snowflakeR --pattern "*.tar.gz"
gh release download --repo Snowflake-Labs/RSnowflake --pattern "*.tar.gz"
```

### Database & Schema

The test reads from `CURRENT_TIMESTAMP()` and optionally writes a small test
table. Any database/schema with CREATE TABLE privilege works.
`setup_notebook()` uses the session's current database/schema by default --
override via the `context:` section in the config YAML if needed.

## Expected Runtime

| Step | First run (tarball) | First run (GitHub) | Cached |
|------|--------------------|--------------------|--------|
| EAI + context setup | ~5 sec | ~5 sec | ~1 sec |
| Bootstrap R environment | ~45 sec | ~45 sec | ~2 sec |
| Install snowflakeR + RSnowflake | **~10 sec** | ~2 min | ~10 sec |
| Run all tests | ~30 sec | ~30 sec | ~30 sec |
| **Total** | **~1.5 min** | **~3.5 min** | **~45 sec** |

## Interpreting Results

Each test section prints `[PASS]` on success. The final Summary cell recaps all
results. Common issues:

- **`Name or service not known` during pip install**: EAI not enabled on the
  notebook service -- run the setup cell, then enable via Connected > Edit > External Access
- **`Insufficient privileges` in setup cell**: Ask admin to run the printed SQL
- **`Object does not exist` SQL error**: Set `context.database` and
  `context.schema` in the config YAML, or verify your session has a default
  database/schema
