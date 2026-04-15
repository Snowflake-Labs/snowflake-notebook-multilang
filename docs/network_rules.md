# Network Rules (EAI)

Snowflake Workspace Notebooks block all outbound network traffic by
default. The toolkit needs to download language runtimes, packages, and
JARs from external hosts, so an External Access Integration (EAI) must
be configured first.

## setup_notebook() (Recommended)

When using `setup_notebook()` from `sfnb_setup.py`, EAI management is
privilege-aware. It discovers existing EAIs, checks for required domains,
and -- **only if the current role has sufficient privileges** -- adds any
that are missing. If the role cannot ALTER or CREATE, the toolkit prints
the exact SQL needed so an administrator can run it instead.

No changes are made that exceed the caller's granted privileges.

### Multi-tier EAI discovery

`setup_notebook()` selects the target EAI using the following priority
(first match wins):

1. **`eai.managed` in YAML config** -- If the user specifies
   `eai: { managed: MY_EAI_NAME }` in the config file, that EAI is used
   as the target. This is the recommended approach when an admin has
   pre-created an EAI for the team.
2. **Convention name** -- If no managed name is specified, the toolkit
   looks for an EAI matching the default supplementary name
   (`MULTILANG_NOTEBOOK_EAI`).
3. **`DESC SERVICE`** -- In non-interactive/scheduled runs where
   `SNOWFLAKE_SERVICE_NAME` is set, this returns the exact EAIs attached
   to the running service.
4. **`.snowflake/settings.json`** -- Best-effort hint from the Workspace
   control plane (not guaranteed to exist).
5. **`SHOW EXTERNAL ACCESS INTEGRATIONS`** -- All enabled EAIs visible
   to the current role.

The toolkit does **not** iterate through all visible EAIs trying each
one -- it selects a single target based on the priority above and
operates on that one EAI only.

### Hybrid EAI management (Hybrid D strategy)

When domains are missing from the selected target EAI:

- **Managed EAI specified** (`eai.managed` in config): The toolkit
  attempts `ALTER NETWORK RULE` on the EAI's existing network rule to
  add the missing domains. If the ALTER succeeds, it re-tests DNS
  resolution to confirm the change is live.
- **ALTER fails** (insufficient privileges on the managed EAI): The
  toolkit falls back to creating a supplementary EAI with only the
  missing domains.
- **No managed EAI found**: The toolkit first tests DNS reachability of
  the required domains. If all resolve (some other EAI already covers
  them), it returns immediately. If domains are unreachable, it attempts
  to CREATE a supplementary EAI (default: `MULTILANG_NOTEBOOK_EAI`).
- **CREATE fails** (insufficient privileges): The complete SQL is
  printed with annotated domain coverage -- showing which domains are
  already covered by other EAIs and which are new -- so the user or
  admin can run it with `ACCOUNTADMIN`.

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

## Custom Mirrors (Artifactory / Nexus)

If your organization routes all package downloads through an internal
artifact repository, the EAI can be simplified to a single domain.
See [custom_mirrors.md](custom_mirrors.md) for full setup instructions.

When `mirrors` is configured in the YAML config, `_domains_from_config()`
automatically replaces the public upstream domains with just the mirror
host(s). For example, setting `conda_channel`, `pypi_index`, and
`cran_mirror` all pointing to `artifactory.snowflake.com` reduces the
EAI to:

```sql
VALUE_LIST = ('artifactory.snowflake.com')
```

### Authenticated Mirrors

When `mirrors.auth_secret` is configured in the YAML, the generated
EAI SQL automatically includes `ALLOWED_AUTHENTICATION_SECRETS` so
the notebook container can access the Snowflake Secret at runtime:

```sql
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION MULTILANG_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (MULTILANG_NOTEBOOK_EGRESS)
  ALLOWED_AUTHENTICATION_SECRETS = (mydb.myschema.artifactory_creds)
  ENABLED = TRUE;
```

This applies to both the `sfnb-multilang` installer's Phase 0
network rule setup and the `sfnb_setup.py` helper's EAI generation.
See [custom_mirrors.md -- Authenticated Mirrors](custom_mirrors.md#authenticated-mirrors)
for the full setup guide.

## Dynamic Application

EAI changes apply **dynamically** to a running notebook. When the SQL
executes, the updated network rules take effect on the next outbound
request -- no kernel restart is needed. This is confirmed by Snowflake
for SPCS-backed services (including Workspace Notebook services).
