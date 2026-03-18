# Network Rules (EAI)

Snowflake Workspace Notebooks block all outbound network traffic by
default. The toolkit needs to download language runtimes, packages, and
JARs from external hosts, so an External Access Integration (EAI) must
be configured first.

## setup_notebook() (Recommended)

When using `setup_notebook()` from `sfnb_setup.py`, EAI management is
fully automatic. It discovers existing EAIs, checks for required domains,
and adds any that are missing.

### Multi-tier EAI discovery

`setup_notebook()` discovers attached EAIs via multiple tiers (first
match wins):

1. **`DESC SERVICE`** -- In non-interactive/scheduled runs where
   `SNOWFLAKE_SERVICE_NAME` is set, this returns the exact EAIs attached
   to the running service.
2. **`.snowflake/settings.json`** -- Best-effort hint from the Workspace
   control plane (not guaranteed to exist).
3. **`SHOW EXTERNAL ACCESS INTEGRATIONS`** -- All EAIs visible to the
   current role.

### Hybrid EAI management (Hybrid D strategy)

When domains are missing:

- **Managed EAI specified** (`eai.managed` in config): The existing EAI's
  network rule is altered via `ALTER NETWORK RULE` to add missing domains.
- **No managed EAI**: A supplementary EAI is created (default name:
  `MULTILANG_NOTEBOOK_EAI`) with only the missing domains.
- **Insufficient privileges**: The complete SQL is printed with annotated
  domain coverage (showing which domains are already covered by other EAIs)
  so the user or admin can run it manually.

### Open EAI detection

If any attached EAI has a network rule of TYPE=IPV4 with `0.0.0.0/0` or
TYPE=HOST_PORT with `0.0.0.0:port`, it is treated as "open" (allows all
egress) and no domain modifications are needed.

## Automatic Setup (sfnb-multilang API)

The `sfnb-multilang` installer includes network rule setup as **Phase 0**,
before any downloads. It attempts to create the network rule and EAI via
the active Snowpark session.

### If you have privileges

The EAI is created automatically. You just need to enable it in Snowsight
after the SQL executes:

**Connected > Edit > External Access > toggle on multilang_notebook_eai > Save**

Once created and attached, the same EAI can be reused across multiple
Workspace Notebooks -- it is a one-time setup per service.

### If you lack privileges

The installer catches the permission error and:

1. Prints the full SQL to the notebook output
2. Saves it to `eai_setup.sql` (configurable via `network_rule.sql_export_path`)
3. Halts the install with clear instructions

Share the `.sql` file with your administrator. They can run it with
`ACCOUNTADMIN` and enable the EAI on your notebook.

## Manual Generation

```python
from sfnb_multilang import generate_eai_sql

sql = generate_eai_sql(languages=["r", "scala"], account="myaccount")
print(sql)
```

Or from the CLI:

```bash
sfnb-setup generate-eai --r --scala --account myaccount
sfnb-setup generate-eai --config config.yaml --output eai_setup.sql
```

## Required Hosts

### Shared (all languages)

| Host | Purpose |
|---|---|
| `micro.mamba.pm` | micromamba binary download |
| `api.anaconda.org` | micromamba redirect target |
| `binstar-cio-packages-prod.s3.amazonaws.com` | Anaconda S3 storage |
| `conda.anaconda.org` | conda-forge package index |
| `repo.anaconda.com` | conda-forge CDN |
| `pypi.org` | PyPI index (sfnb-multilang, rpy2, JPype1) |
| `files.pythonhosted.org` | PyPI file downloads |
| `github.com` | GitHub repos and source archives |
| `api.github.com` | GitHub API (pak dependency resolution) |
| `codeload.github.com` | GitHub source archives |
| `objects.githubusercontent.com` | GitHub raw content |
| `release-assets.githubusercontent.com` | GitHub release asset downloads |

### R

| Host | When |
|---|---|
| `cloud.r-project.org` | CRAN packages configured |
| `bioconductor.org` | pak initialization |
| `community.r-multiverse.org` | ADBC enabled |
| `cdn.r-universe.dev` | ADBC enabled |
| `proxy.golang.org` | ADBC enabled |
| `storage.googleapis.com` | ADBC enabled |
| `sum.golang.org` | ADBC enabled |

### Scala/Java

| Host | Purpose |
|---|---|
| `repo1.maven.org` | Maven Central |

### Julia

| Host | Purpose |
|---|---|
| `sfc-repo.snowflakecomputing.com` | ODBC driver (if enabled) |

**Note:** Julia uses `JULIA_PKG_SERVER=""` to bypass the Julia package
server redirect chain (`pkg.julialang.org` -> `storage.julialang.net`)
which has known SPCS DNS issues. All packages are cloned via Git from
`github.com` instead.

## Dynamic Application

EAI changes apply **dynamically** to a running notebook. When the SQL
executes, the updated network rules take effect on the next outbound
request -- no kernel restart is needed. This is confirmed by Snowflake
for SPCS-backed services (including Workspace Notebook services).
