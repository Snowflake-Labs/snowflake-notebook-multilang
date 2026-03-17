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
| `eai_helper.py` | Self-contained EAI management (no pip dependencies) |
| `sfnb_setup.py` | Bootstrap -- pip-installs `sfnb-multilang` and sets up R |
| `r_smoke_test_config.yaml` | Conda/CRAN package list for the R environment |
| `notebook_config.yaml.template` | Template -- copy to `notebook_config.yaml` and edit |
| `workspace_r_smoke_test.ipynb` | The test notebook |

## Setup

### EAI Setup

Workspace Notebooks block all outbound network traffic by default. Section 0
of the notebook uses `eai_helper.py` to ensure the EAI has all domains needed
for the configured languages.

**How it works:**

- **EAI already attached to your service?** Section 0 introspects its network
  rule, adds any missing domains via `ALTER NETWORK RULE`, and changes take
  effect immediately -- no restart needed.
- **No EAI attached yet?** Section 0 creates the EAI + network rule and prints
  instructions for the one-time manual attachment via the Snowsight UI.
- **No privileges to create/alter?** Section 0 prints the complete SQL for
  your admin.

**EAI name resolution:** Section 0 checks `notebook_config.yaml` for an
explicit `eai.name`, then falls back to the convention name
`multilang_notebook_eai`.

### First-time steps (no EAI yet)

1. Upload all files to a Workspace Notebook
2. Copy `notebook_config.yaml.template` to `notebook_config.yaml` and edit it
3. Open `workspace_r_smoke_test.ipynb`
4. **Run Section 0** -- creates the EAI
5. Click **Connected** (top-left toolbar)
6. Hover over your service name and click **Edit**
7. Scroll to **External Access** > toggle **ON** the EAI > **Save**
8. Service restarts automatically
9. **Re-run Section 0** (it updates the network rule), then run **Section 1+**

### Returning steps (EAI already attached)

1. Open the notebook
2. Run **Section 0** (verifies/updates domains -- takes ~1 sec)
3. Run from **Section 1** onward

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
| `github.com` | GitHub repos (sfnb-multilang, snowflakeR, RSnowflake) |
| `api.github.com` | GitHub API (pak dependency resolution) |
| `codeload.github.com` | GitHub source archives |
| `objects.githubusercontent.com` | GitHub raw content |
| `pypi.org` | PyPI index (sfnb-multilang, rpy2) |
| `files.pythonhosted.org` | PyPI file downloads |

## Prerequisites

### RSnowflake Authentication

RSnowflake uses the Snowflake SQL API v2. In Workspace Notebooks the built-in
SPCS OAuth token (`/snowflake/session/token`) is used automatically -- no
Programmatic Access Token (PAT) is required.

### Faster R Package Installation (optional)

By default, `snowflakeR` and `RSnowflake` are installed from GitHub via `pak`,
which takes ~2 minutes on a fresh container. For faster installs, upload the
pre-built tarballs to your Workspace alongside the notebook:

- `snowflakeR_*.tar.gz`
- `RSnowflake_*.tar.gz`

When both tarballs are present the notebook installs from them directly
(~10 seconds), skipping the GitHub download entirely.

**Where to get the tarballs:** Download from GitHub Releases:

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

## Expected Runtime

| Step | First run | Cached |
|------|-----------|--------|
| EAI setup (Section 0) | ~5 sec | skip |
| Bootstrap R environment | ~3 min | ~2 sec |
| Install snowflakeR + RSnowflake | ~2 min | ~10 sec |
| Run all tests | ~30 sec | ~30 sec |
| **Total** | **~5.5 min** | **~45 sec** |

## Interpreting Results

Each test section prints `[PASS]` on success. The final Summary cell recaps all
results. Common issues:

- **`Name or service not known` during pip install**: EAI not enabled on the
  notebook service -- run Section 0, then enable via Connected > Edit > External Access
- **`Insufficient privileges` in Section 0**: Ask admin to run the printed SQL
- **`sfr_load_notebook_config` error**: Check your `notebook_config.yaml`
