# Network Rules (EAI)

Snowflake Workspace Notebooks block all outbound network traffic by
default. The toolkit needs to download language runtimes, packages, and
JARs from external hosts, so an External Access Integration (EAI) must
be configured first.

## Automatic Setup

The installer includes network rule setup as **Phase 0**, before any
downloads. It attempts to create the network rule and EAI via the active
Snowpark session.

### If you have privileges

The EAI is created automatically. You just need to enable it in Snowsight
after the SQL executes:

**Notebook settings > External access > toggle on multilang_notebook_eai**

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

### R

| Host | When |
|---|---|
| `cloud.r-project.org` | CRAN packages configured |
| `community.r-multiverse.org` | ADBC enabled |
| `cdn.r-universe.dev` | ADBC enabled |
| `proxy.golang.org` | ADBC enabled |
| `storage.googleapis.com` | ADBC enabled |
| `sum.golang.org` | ADBC enabled |

### Scala/Java

| Host | Purpose |
|---|---|
| `repo1.maven.org` | Maven Central |
| `github.com` | coursier download |
| `release-assets.githubusercontent.com` | GitHub redirect |
| `objects.githubusercontent.com` | GitHub content |
| `pypi.org` | JPype1 |
| `files.pythonhosted.org` | pip files |

### Julia

| Host | Purpose |
|---|---|
| `github.com` | Registry + package source (Git clone) |
| `pypi.org` | juliacall |
| `files.pythonhosted.org` | pip files |
| `sfc-repo.snowflakecomputing.com` | ODBC driver (if enabled) |

**Note:** Julia uses `JULIA_PKG_SERVER=""` to bypass the Julia package
server redirect chain (`pkg.julialang.org` -> `storage.julialang.net`)
which has known SPCS DNS issues. All packages are cloned via Git from
`github.com` instead.

## Dynamic Application

EAI changes apply **dynamically** to a running notebook. When the SQL
executes, the updated network rules take effect on the next outbound
request -- no kernel restart is needed.
