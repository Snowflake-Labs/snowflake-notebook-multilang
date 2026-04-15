# Artifact Repository & Conda Channel Compliance for multi-language (R) Notebooks and snowflakeR

**Date:** 2026-03-27 (updated 2026-04-15; lives in this repo under `docs/`)
**Audience:** Snowflake accounts using Workspace notebooks with **snowflakeR**
(or RSnowflake) and optional corporate package mirrors.

## Summary

This guide explains two related capabilities you can turn on with YAML
beside your notebooks:

1. **Artifact repository proxying** -- route all *notebook setup*
   package downloads through a corporate proxy (JFrog Artifactory,
   Sonatype Nexus, or equivalent).

2. **Model Registry conda channel enforcement** -- record inference
   container conda dependencies so they resolve from `conda-forge` (or
   another approved channel), avoiding inadvertent use of Anaconda Inc.
   commercial channels.

Both are **configuration-only**: you do not edit the `snowflakeR` or
multilang package sources; you point `setup_notebook()` at a YAML file
that carries the policy.

The settings are aimed at organizations in regulated industries
(banking, insurance, healthcare) where:

- All external packages must pass through an internal proxy for
  scanning and audit before entering the network
- Direct access to public package registries (PyPI, conda-forge, CRAN)
  is blocked by policy
- TLS inspection (SSL interception) is in place, requiring custom CA
  certificate bundles
- Use of Anaconda Inc. commercial channels (including the Snowflake
  Anaconda Channel) must be avoided due to licensing restrictions

## What you can configure

### YAML section: `mirrors`

A single block in the existing `_config.yaml` file controls all mirror
routing:

```yaml
mirrors:
  conda_channel: https://artifactory.example.com/conda-forge-remote
  pypi_index:    https://artifactory.example.com/api/pypi/pypi-remote/simple
  cran_mirror:   https://artifactory.example.com/cran-remote
  micromamba_url: https://artifactory.example.com/generic-local/micromamba-1.5.12-0-linux-64
  ssl_cert_path: /etc/ssl/certs/corporate-ca-bundle.crt
  # Optional: Snowflake PASSWORD secret (Workspace Notebook Secrets preview) — see §2b
  # auth_secret: "mydb.myschema.artifactory_creds"
```

When these values are set, every download in the installation pipeline
(micromamba binary, conda packages, pip packages, CRAN packages) is
redirected to the mirror URL. No changes to notebook helper or package
source code are required—only YAML.

### Simplified network rules (EAI)

Without mirrors, the External Access Integration requires ~15 public
domains. With mirrors configured pointing to a single Artifactory host,
the EAI reduces to:

```sql
VALUE_LIST = ('artifactory.example.com')
```

This is a significant security posture improvement -- the notebook can
only reach the corporate proxy, and the proxy controls what packages
are available.

### TLS inspection support

The `ssl_cert_path` setting is threaded through every download mechanism:

| Tool | How the cert is applied |
|---|---|
| micromamba | `--ssl-verify /path/to/cert.crt` |
| pip | `--cert /path/to/cert.crt` |
| conda | `--ssl-verify /path/to/cert.crt` on `micromamba create/install` |
| R (CRAN) | `CURL_CA_BUNDLE` environment variable |
| urllib (tarball downloads) | `ssl.SSLContext` with `load_verify_locations()` |

## What you need to do

### 1. Set up repository proxies in Artifactory

Four remote repositories are needed (or fewer if some package types
are not used):

| Repository type | Upstream URL | Artifactory repo type |
|---|---|---|
| **Conda** (conda-forge) | `https://conda.anaconda.org/conda-forge` | Remote - Conda |
| **PyPI** | `https://pypi.org` | Remote - PyPI |
| **CRAN** | `https://cloud.r-project.org` | Remote - CRAN |
| **Generic** (micromamba binary) | `https://github.com/mamba-org/micromamba-releases` | Remote - Generic or Local |

The Artifactory admin creates these as remote-type repositories. Once
created, Artifactory transparently proxies, caches, and scans packages
on first download.

### 2. Add the mirrors section to your config YAML

Replace the example domain with your organisation’s Artifactory hostname:

```yaml
mirrors:
  conda_channel: https://artifactory.customer.com/conda-forge-remote
  pypi_index:    https://artifactory.customer.com/api/pypi/pypi-remote/simple
  cran_mirror:   https://artifactory.customer.com/cran-remote
  micromamba_url: https://artifactory.customer.com/generic-local/micromamba-1.5.12-0-linux-64
  ssl_cert_path: /etc/ssl/certs/corporate-ca-bundle.crt
```

The `ssl_cert_path` is only needed if TLS inspection is active. It must
be a **normal file path inside your Workspace session** that exists when
`setup_notebook()` runs (the helper checks `os.path.isfile`). Snowflake
manages the **base Workspace container image**, so you should not assume
you can permanently install a CA into `/etc/ssl/certs/` on that image.

Common patterns:

- **Git-backed Workspace** — commit the corporate CA PEM (or merged
  bundle) in the repository that backs the notebook and set
  `ssl_cert_path` to the path as mounted in Workspace (often under your
  repo root next to the config YAML).
- **File you upload or copy** — add the PEM through Workspace/Snowsight,
  or use a short bootstrap cell (before `setup_notebook()`) to fetch it
  from a Snowflake stage into a writable path such as `/tmp/corp-ca.pem`,
  then point `ssl_cert_path` at that path.
- **Default trust store** — if the Snowflake-provided image already trusts
  your inspection CA (uncommon), you may not need `ssl_cert_path` at all.

In enterprises the CA PEM is usually **not** “on the public internet”:
PKI publishes it on the **intranet**, security adds it to a **private Git
repo** (`certs/corp-root.pem`), or automation **`PUT`s** it to a
**Snowflake internal stage** for notebooks to read. You then **copy or
download** it into the Workspace session (Git mount path, upload,
`GET`/`curl` to `/tmp/…`) in a **cell before** `setup_notebook()`. A
`curl` to an internal URL only works if your notebook **EAI** allows that
host. More detail: **[custom_mirrors.md](custom_mirrors.md#how-enterprises-usually-supply-the-ca)**.

### 2b. Workspace Notebook Secrets (preview) and `auth_secret`

When Artifactory (or another mirror) requires authentication, store
credentials in a Snowflake **PASSWORD** secret and reference it from
the YAML. `setup_notebook()` reads the secret at bootstrap (Snowpark
`get_username_password()` or `/secrets/db/schema/name/` mount) and
injects basic-auth into mirror URLs. Credentials never belong in the
notebook or config file.

**1. Create the secret (admin, one-time):**

```sql
CREATE OR REPLACE SECRET mydb.myschema.artifactory_creds
  TYPE = PASSWORD
  USERNAME = 'artifactory-service-account'   -- or deploy-token user
  PASSWORD = '<artifactory-api-key-or-identity-token>';
```

**2. Attach the secret to the Workspace notebook service:** When
creating or editing the notebook service in Snowsight, the secret must
be selected in addition to SQL grants. Use the Workspace / Snowsight
flow your account provides (fully qualified secret name
`DB.SCHEMA.SECRET_NAME` where applicable).

**3. Add `auth_secret` to the same `mirrors` block** (fully qualified
name; `db.schema.name` or `db/schema/name` both work):

```yaml
mirrors:
  conda_channel: https://artifactory.customer.com/conda-forge-remote
  pypi_index:    https://artifactory.customer.com/api/pypi/pypi-remote/simple
  cran_mirror:   https://artifactory.customer.com/cran-remote
  micromamba_url: https://artifactory.customer.com/generic-local/micromamba-1.5.12-0-linux-64
  ssl_cert_path: /etc/ssl/certs/corporate-ca-bundle.crt
  auth_secret: "mydb.myschema.artifactory_creds"
```

**Identity model:** This pattern authenticates **bootstrap traffic from
the notebook container** using the secret attached to the service
(typically a **service account** or shared deploy token). It does **not**
map each Snowflake `CURRENT_USER()` to a distinct Artifactory user
automatically. If the organisation requires per-human Artifactory
identity, that needs a separate design (for example OAuth with an
external IdP and repository policies).

For mirrors, EAI patterns, and troubleshooting, see **[custom_mirrors.md](custom_mirrors.md)** in this repository.

### 3. Configure the EAI for outbound access to Artifactory

The simplest EAI when mirrors are configured **without** mirror
authentication:

```sql
CREATE OR REPLACE NETWORK RULE MULTILANG_NOTEBOOK_EGRESS
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = ('artifactory.customer.com');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION MULTILANG_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (MULTILANG_NOTEBOOK_EGRESS)
  ENABLED = TRUE;
```

When `mirrors.auth_secret` is set, the EAI must also allow the secret
for authenticated egress (Snowflake adds `ALLOWED_AUTHENTICATION_SECRETS`).
Example:

```sql
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION MULTILANG_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (MULTILANG_NOTEBOOK_EGRESS)
  ALLOWED_AUTHENTICATION_SECRETS = (mydb.myschema.artifactory_creds)
  ENABLED = TRUE;
```

When you run `setup_notebook()` with this YAML, the notebook helper reads
your mirror host, derives the right network rules, and—if you set
`auth_secret`—includes `ALLOWED_AUTHENTICATION_SECRETS` in the suggested
External Access Integration SQL. If your role cannot execute that DDL,
copy the printed SQL and ask an account administrator (for example
someone with `ACCOUNTADMIN` or equivalent) to run it.

### 4. Upload the micromamba binary (if using `micromamba_url`)

The micromamba binary (single file, ~10 MB) needs to be uploaded to
the Artifactory generic repository once:

```bash
# Download from GitHub
curl -L -o micromamba \
  https://github.com/mamba-org/micromamba-releases/releases/download/1.5.12-0/micromamba-linux-64

# Upload to Artifactory
curl -u admin:password -T micromamba \
  https://artifactory.customer.com/generic-local/micromamba-1.5.12-0-linux-64
```

This is a one-time step. The remote conda, PyPI, and CRAN repositories
populate their caches automatically on first use.

## What Does Not Change

- **Notebook code** -- `setup_notebook()` works identically; it reads
  the mirrors and registry config and routes downloads accordingly.
- **R package installation** -- `install.packages()` calls and
  conda-forge installs both respect the mirrors transparently.
- **Existing notebooks** -- If `mirrors` and `registry` are not set in
  your YAML, notebook setup behaves as before (public upstream
  sources). For SPCS model services (always the case for R models),
  the default conda channel is already `conda-forge`. Warehouse
  models default to the Snowflake Anaconda Channel.

---

## Model Registry Conda Channel Enforcement

### Understanding Package Resolution: When and Where

A critical distinction for compliance: `log_model()` and `create_service()`
are separate steps with very different effects.

**`log_model()` records dependency *specifications* only.** It writes the
list of packages (e.g. `conda-forge::r-base==4.5.2`) into the model's
metadata in the Model Registry stage. No packages are downloaded at this
point.

**`create_service()` triggers `MODEL_BUILD`, which downloads and installs
packages.** This is when conda/pip resolution actually happens and where
channel enforcement matters.

### Default Conda Channels by Target Platform

| Target | Default conda source | How to override | Notes |
|---|---|---|---|
| **Warehouse** (UDFs/Sprocs) | **Snowflake Anaconda Channel** (curated subset licensed by Snowflake from Anaconda Inc.) | `channel::package` prefix in `conda_dependencies` | The Snowflake Anaconda Channel is licensed for in-Snowflake use. However, if the organisation has a blanket "no Anaconda" policy, explicit `conda-forge::` prefixes are needed. |
| **SPCS** (model services) | **conda-forge** | `channel::package` prefix in `conda_dependencies` | Already safe by default; explicit prefix still recommended for auditability. |

### EAI and MODEL_BUILD

There are two distinct surfaces where packages are resolved, each with
different EAI controls:

| Surface | What controls package resolution | EAI involved? |
|---|---|---|
| **Notebook setup** (micromamba, pip, CRAN) | Runs in the notebook container; outbound traffic is governed by the notebook's EAI | **Yes** -- blocking `conda.anaconda.org` and `repo.anaconda.com` prevents the notebook from reaching Anaconda commercial channels |
| **MODEL_BUILD** (SPCS model services) | `create_service()` triggers a platform build job that resolves `conda_dependencies` and `pip_requirements` | **Yes, if configured** -- the `build_external_access_integrations` parameter on `create_service()` attaches an EAI to the build job, controlling its outbound network access |
| **MODEL_BUILD** (Warehouse models) | Package resolution runs within Snowflake's warehouse infrastructure | **No** -- warehouse model resolution uses Snowflake's internal package infrastructure; EAI does not apply |

**Default behaviour when no build EAI is specified:**

When `build_external_access_integrations` is not set, MODEL_BUILD for
SPCS resolves packages using these channels:

- `conda-forge`
- `nodefaults`

Snowflake's platform infrastructure provides built-in network access to
conda-forge for the build job. **No EAI is required for this to work.**
The `defaults` channel (Anaconda commercial) is explicitly excluded
because the Snowflake conda channel is only available in warehouses,
and the defaults channel requires accepting Anaconda terms of use --
which is not possible during an automated build.

This means SPCS model builds (including all R models) are already
compliant with "no commercial Anaconda" policies by default.

**Optional: `build_external_access_integrations`**

The `create_service()` function accepts a `build_external_access_integrations`
parameter that explicitly attaches an EAI to the MODEL_BUILD process for
SPCS targets. That lets you attach a restrictive EAI so MODEL_BUILD may
only reach approved hosts (for example your Artifactory conda proxy),
and package resolution is limited to those hosts.

```python
# Example: restrictive build EAI for SPCS model service
mv.create_service(
    service_name="MY_SERVICE",
    ...,
    build_external_access_integrations=["APPROVED_REPOS_ONLY_EAI"]
)
```

This provides **network-level enforcement** at build time, complementing the
`channel::` prefix approach (which provides spec-level enforcement at
`log_model()` time). It is an additional defence-in-depth measure, not a
requirement for conda-forge compliance.

**What this means in practice:**

- **For SPCS model services** -- two layers of enforcement are available:
  (1) `registry.conda_channel` in the YAML config prefixes all deps with
  `conda-forge::` at `log_model()` time, and (2) a restrictive build EAI
  on `create_service()` limits which hosts MODEL_BUILD can reach at build
  time. Together these provide defence-in-depth.
- **For Warehouse models** -- EAI does not apply to package resolution.
  The `channel::` prefix on `conda_dependencies` is the primary mechanism.
  Additionally, **Packages Policies** (see below) provide account-level
  allowlist/blocklist controls.

### Snowflake Packages Policies (Warehouse)

Snowflake provides an account-level **Packages Policy** feature that lets
administrators create allowlists and blocklists for both Anaconda and
Artifact Repository packages. This applies to warehouse workloads
(UDFs, stored procedures, and warehouse-target Model Registry models).

```sql
CREATE PACKAGES POLICY mydb.myschema.approved_packages
  LANGUAGE PYTHON
  ALLOWLIST = ('numpy', 'pandas>=2.0', 'scikit-learn', ...)
  BLOCKLIST = ('bad_package', ...)
  COMMENT = 'Only approved packages for production';

ALTER ACCOUNT SET PACKAGES POLICY mydb.myschema.approved_packages;
```

This gives account admins fine-grained control over exactly which
packages (and versions) are permitted, applied at the account level. It
works alongside the conda channel prefixing -- the packages policy
controls *which packages* are allowed, while the channel prefix controls
*where they come from*.

### Snowflake Artifact Repository (Warehouse -- Public Preview)

For **pip dependencies** on warehouse-target models, the Artifact
Repository feature (`artifact_repository_map` on `log_model()`) allows
sourcing packages from PyPI instead of the Anaconda channel. The
built-in repository `snowflake.snowpark.pypi_shared_repository` points
to public PyPI.

**Limitation:** Access to private repositories (e.g. a corporate
Artifactory PyPI proxy) is **not supported** as of March 2026. The
feature currently only works with public PyPI. You cannot route
warehouse-model pip dependencies through a private Artifactory PyPI
mirror using this mechanism alone.

If you need private PyPI for warehouse models, upload packages to a
Snowflake stage and import them, or follow future Snowflake documentation
for private artifact repositories when available.

### YAML section: `registry`

A new `registry:` block in the same config YAML controls the Model
Registry conda channel policy:

```yaml
registry:
  conda_channel: conda-forge
  conda_channel_strict: true
```

When set, `setup_notebook()` exports environment variables that
snowflakeR's `sfr_model_registry()` and `sfr_log_model()` read
automatically:

| Env var | Value | Effect |
|---|---|---|
| `SFR_CONDA_CHANNEL` | `conda-forge` | Every conda dep without a `channel::` prefix is rewritten as `conda-forge::pkg` |
| `SFR_CONDA_CHANNEL_STRICT` | `true` | Users cannot override the channel at call-time; attempts to use a different channel raise an error |

### How it works end-to-end

```
─── NOTEBOOK (enforcement at log_model time) ──────────────────────

_config.yaml                    ← administrators maintain registry: section
       │
       ▼
setup_notebook()                ← exports SFR_CONDA_CHANNEL env vars
       │
       ▼
sfr_model_registry(conn)        ← picks up env vars as defaults
       │                           conda_channel = "conda-forge"
       │                           conda_channel_strict = TRUE
       ▼
sfr_log_model(reg, model, ...)  ← strict mode prevents override
       │
       ▼
Python bridge                   ← auto-prefixes all deps:
                                   "r-base==4.5.2" → "conda-forge::r-base==4.5.2"
                                   "rpy2>=3.5"     → "conda-forge::rpy2>=3.5"
       │
       ▼
Registry.log_model()            ← stores prefixed dep SPECS in registry
                                   (no packages downloaded yet)

─── SNOWFLAKE PLATFORM (package download at create_service time) ──

       ├─── [SPCS path] ──────────────────────────────────────────────┐
       │                                                               ▼
       │    sfr_deploy_model() /                               MODEL_BUILD
       │    create_service()                                   reads stored specs,
       │                                                       resolves + installs
       │    Network: DIRECT to                                 packages from
       │    conda.anaconda.org/conda-forge                     conda-forge
       │    (Snowflake platform infra)                         (NOT through
       │                                                        Artifactory mirror)
       │    Optional: build EAI can
       │    restrict outbound hosts
       │
       └─── [Warehouse path] ─────────────────────────────────────────┐
                                                                       ▼
            First inference triggers                           Package resolution
            package resolution                                 uses Snowflake's
                                                               internal infra.
                                                               channel:: prefix
                                                               + Packages Policy
                                                               enforce compliance.
```

### Enforcement levels

| Level | How | Who can override? |
|---|---|---|
| **Per-call** | `sfr_log_model(..., conda_channel = "conda-forge")` | Any user (no enforcement) |
| **Registry default** | `sfr_model_registry(conn, conda_channel = "conda-forge")` | User can override per-call |
| **Registry strict** | `sfr_model_registry(conn, conda_channel = "conda-forge", conda_channel_strict = TRUE)` | Nobody -- error on override attempt |
| **Config YAML + strict** | `registry: { conda_channel: conda-forge, conda_channel_strict: true }` in YAML | Notebook authors cannot override; administrators own the shared config file and env vars are applied before user cells run |
| **Env var (fallback)** | `SFR_CONDA_CHANNEL=conda-forge` + `SFR_CONDA_CHANNEL_STRICT=true` | Env var is read by both `sfr_model_registry()` and `sfr_log_model()` even without a registry object |

For strict compliance, the recommended level is **Config YAML +
strict**. Administrators maintain the shared config file; notebook
authors cannot bypass the channel policy from their own cells.

### What you need to do (registry, in addition to mirrors)

Add the `registry:` block to the same config YAML you already use for
mirrors:

```yaml
# Already existing:
mirrors:
  conda_channel: https://artifactory.customer.com/conda-forge-remote
  pypi_index:    https://artifactory.customer.com/api/pypi/pypi-remote/simple
  ...

# New -- add this:
registry:
  conda_channel: conda-forge
  conda_channel_strict: true
```

No notebook code changes. No new EAI rules. The policy takes effect
the next time `setup_notebook()` runs.

### Distinction between `mirrors.conda_channel` and `registry.conda_channel`

These are related but independent settings that control different
stages and different network paths:

| | `mirrors.conda_channel` | `registry.conda_channel` |
|---|---|---|
| **Controls** | Where the **notebook** downloads conda packages during `setup_notebook()` | Which **channel name** is prefixed on `conda_dependencies` stored by `log_model()` |
| **Value type** | A URL (e.g. `https://artifactory.example.com/conda-forge-remote`) | A conda channel name (e.g. `conda-forge`) |
| **When it acts** | Notebook setup time (micromamba/conda downloads) | `log_model()` time (dep spec writing) |
| **Network path** | Notebook container → notebook EAI → Artifactory proxy | N/A at `log_model()` time (specs only). At `create_service()` time: MODEL_BUILD → **direct to `conda.anaconda.org/conda-forge`** via Snowflake platform infra |
| **Goes through Artifactory?** | **Yes** -- all notebook conda downloads route through the mirror URL | **No** -- MODEL_BUILD resolves `conda-forge` directly; it has no knowledge of the `mirrors` config |

### Network paths: notebook setup vs MODEL_BUILD

This is an important architectural distinction:

```
NOTEBOOK SETUP (setup_notebook)
  conda packages → mirrors.conda_channel URL → Artifactory proxy
  pip packages   → mirrors.pypi_index URL    → Artifactory proxy
  CRAN packages  → mirrors.cran_mirror URL   → Artifactory proxy
  Network controlled by: notebook EAI

MODEL_BUILD (create_service for SPCS)
  conda-forge::pkg → conda.anaconda.org/conda-forge → DIRECT
  Network controlled by: Snowflake platform infra
                         (or build_external_access_integrations if set)
```

MODEL_BUILD is a Snowflake platform job that runs outside the notebook
container. It does **not** read the YAML config, does not know about
`mirrors.conda_channel`, and resolves `conda-forge` directly against
`conda.anaconda.org/conda-forge` using Snowflake's built-in network
path.

**Implications if you require every download to go through Artifactory**

If your organisation requires that *every* package download (including
MODEL_BUILD) passes through their corporate proxy for scanning and
audit, the current architecture has a gap:

- The `registry.conda_channel` setting ensures deps are *labelled*
  `conda-forge::`, but MODEL_BUILD still fetches them directly from
  `conda.anaconda.org/conda-forge`, not through Artifactory.
- A `build_external_access_integrations` EAI could restrict which
  hosts MODEL_BUILD can reach, but it cannot currently redirect
  `conda-forge` resolution through an Artifactory conda proxy URL.
- Blocking `conda.anaconda.org` in the build EAI while only allowing
  the Artifactory host would **break** MODEL_BUILD unless the platform
  supports channel URL remapping (see open questions).

**If your concern is only "no commercial Anaconda"** (while direct
access to conda-forge is acceptable), this is not a problem.
`conda-forge` is a community-maintained channel hosted at
`conda.anaconda.org/conda-forge` -- it is not a commercial Anaconda
channel, and no Anaconda Inc. licensing applies.

In practice, many teams set both: `mirrors.conda_channel` routes
notebook setup through an Artifactory conda-forge proxy, and
`registry.conda_channel` ensures inference container deps are prefixed
with `conda-forge::` for auditability. The direct MODEL_BUILD path to
conda-forge is acceptable for "no commercial Anaconda" compliance but
does not satisfy "all downloads through Artifactory" requirements.

## Authenticated Mirror Access (Artifactory without anonymous access)

Most regulated organisations disable anonymous access on their
Artifactory instance (JFrog's default and recommended security
posture). This means every client that connects needs to authenticate,
typically with an API key or access token.

### Current gap

The `mirrors` config in the YAML currently passes bare URLs to
pip, conda, micromamba, and CRAN. There is no built-in support for
passing authentication tokens separately. The available workarounds
today are:

| Approach | How | Drawback |
|---|---|---|
| **Embed credentials in URL** | `https://user:token@artifactory.example.com/...` in YAML | Credentials in a config file -- security risk if committed to Git or stored in a stage |
| **Credential UDF** | Create a Python UDF (runs on warehouse where `_snowflake` Secret API is available) that reads a Snowflake Secret and returns the credentials; `setup_notebook()` calls it via the Snowpark session | Requires a pre-created UDF; adds a warehouse call during setup |

Neither is ideal. The clean solution is to read credentials from a
**Snowflake Secret** directly within the notebook during
`setup_notebook()`.

### Snowflake Secrets in Workspace Notebooks (private preview)

Snowflake Secrets are now available in Workspace Notebooks as a
private preview feature (April 2026). The Snowpark Secrets API
provides typed accessors for each secret type:

```python
from snowflake.snowpark.secrets import get_username_password

creds = get_username_password('mydb/myschema/artifactory_creds')
# creds.username, creds.password
```

Secrets are also mounted as files in the notebook container at
`/secrets/<db>/<schema>/<name>/{username,password}`, providing a
fallback path if the Snowpark API is unavailable.

**Admin setup (one-time):**

```sql
-- 1. Store Artifactory credentials as a Snowflake Secret
CREATE SECRET mydb.myschema.artifactory_creds
  TYPE = PASSWORD
  USERNAME = 'deploy-token'
  PASSWORD = '<artifactory-api-key>';

-- 2. Include the secret in the EAI
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION MULTILANG_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (MULTILANG_NOTEBOOK_EGRESS)
  ALLOWED_AUTHENTICATION_SECRETS = (mydb.myschema.artifactory_creds)
  ENABLED = TRUE;

-- 3. When creating the notebook service, select the secret and EAI
```

**YAML config -- reference the secret name, not credentials:**

```yaml
mirrors:
  conda_channel: https://artifactory.example.com/conda-forge-remote
  pypi_index: https://artifactory.example.com/api/pypi/pypi-remote/simple
  cran_mirror: https://artifactory.example.com/cran-remote
  auth_secret: mydb.myschema.artifactory_creds
```

**In `setup_notebook()`:**

`setup_notebook()` automatically reads the secret and injects
`username:password` basic auth into all mirror URLs. The credential
reading strategy uses two fallback layers:

1. Snowpark Secrets API: `get_username_password('db/schema/name')`
2. Container mount path: `/secrets/db/schema/name/{username,password}`

If neither succeeds, a warning is logged and mirror URLs remain
unauthenticated.

This keeps credentials out of the YAML, managed via Snowflake's
secret infrastructure (RBAC, audit, rotation). The platform
administrator owns the secret object while notebook authors never see
the raw token.

### How `auth_secret` behaves in the notebook helper

With a supported **snowflake-notebook-multilang** / `sfnb_setup.py`
build (for example from Snowflake-Labs releases):

1. You set `mirrors.auth_secret` in YAML to a fully qualified Snowflake
   `PASSWORD` secret.
2. `setup_notebook()` (and the batch installer path) read credentials via
   the Snowpark Secrets API, with a fallback to the `/secrets/...` mount
   in the notebook container, and inject HTTP basic auth into pip,
   conda/micromamba, CRAN, and tarball download URLs.
3. Generated External Access Integration SQL includes
   `ALLOWED_AUTHENTICATION_SECRETS` when `auth_secret` is present.
4. Credentials are masked in setup logs.
5. Full setup steps and troubleshooting are in **[custom_mirrors.md](custom_mirrors.md)**.

### Impact on MODEL_BUILD

Authenticated Artifactory access via Secrets only applies to the
**notebook setup** path today. MODEL_BUILD (SPCS) goes direct to
conda-forge via Snowflake platform infrastructure and has no mechanism
to carry Artifactory credentials. This is not a problem for "no
commercial Anaconda" compliance, but it means MODEL_BUILD package
downloads cannot be routed through an authenticated Artifactory proxy
today.

**Update (April 2026):** Snowflake Model Registry has announced work on
**private artifact repository integration for SPCS served models** for
the same period. If delivered, MODEL_BUILD could resolve packages through
your Artifactory instance instead of going direct to conda-forge. See
"Private Artifact Repository for SPCS MODEL_BUILD" below for expected
capabilities and open design points.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `CondaHTTPError` or `ResolvePackageNotFound` | Conda remote repo not configured or package not yet cached | Verify the Artifactory conda remote repo URL and that it proxies `conda-forge` |
| `pip install` returns 403 | PyPI remote repo requires authentication or package blocked by policy | Check Artifactory access permissions; verify the package has passed security scanning |
| `CERTIFICATE_VERIFY_FAILED` | TLS inspection active but `ssl_cert_path` not set or wrong path | Set `ssl_cert_path` to the corporate CA bundle; verify the file exists in the container |
| `Connection refused` on Artifactory host | EAI does not include the Artifactory domain | Add the Artifactory hostname to the EAI network rule |
| Package cached but blocked (403 after initial pull) | Artifactory security scan pending or policy violation | Check Artifactory's "Xray" scan results; the artifact may need manual approval |
| MODEL_BUILD (Warehouse) resolves from Snowflake Anaconda Channel despite EAI blocking Anaconda domains | Warehouse MODEL_BUILD uses Snowflake's internal package infrastructure; EAI does not apply | Set `registry.conda_channel: conda-forge` in the config YAML to prefix all deps with `conda-forge::`. Consider also using a Packages Policy allowlist at the account level. |
| MODEL_BUILD (SPCS) pulls from unwanted channel | `build_external_access_integrations` not set, or the build EAI allows too many hosts | Add a restrictive `build_external_access_integrations` to the `create_service()` call that only permits the approved package hosts |
| MODEL_BUILD (SPCS) fails with network/resolution errors after adding build EAI | Build EAI is too restrictive; MODEL_BUILD cannot reach `conda-forge` or the Artifactory proxy | Verify the build EAI allows the required hosts (e.g. `conda.anaconda.org` for conda-forge, or the Artifactory hostname). Open question: whether MODEL_BUILD can remap `conda-forge` channel resolution to a custom Artifactory conda-channel proxy URL. |
| `sfr_log_model()` error: "Registry channel policy is strict" | User tried to override the channel in strict mode | Remove the `conda_channel` argument from `sfr_log_model()` or remove foreign `channel::` prefixes from `conda_deps` |
| `sfr_log_model()` error: "conda_deps contains foreign channels" | User passed deps like `defaults::numpy` while strict mode enforces `conda-forge` | Change the prefix to `conda-forge::numpy` or remove the prefix (it will be auto-added) |
| Packages Policy blocks a required package | Package or version not in the account's `ALLOWLIST` | Ask the account admin to add the package to the Packages Policy `ALLOWLIST` |

## Recommended Configuration

### SPCS model services (R models via snowflakeR)

**Default behaviour (no configuration needed):** When no
`build_external_access_integrations` is specified, MODEL_BUILD for SPCS
uses `conda-forge` and `nodefaults` as its channels. Snowflake provides
built-in network access to conda-forge for the build job -- no EAI is
required. Packages are fetched **directly** from
`conda.anaconda.org/conda-forge` (not through any Artifactory mirror).
This means **R models deployed via snowflakeR are already compliant by
default** with a "no commercial Anaconda" policy. No packages will be
pulled from Anaconda Inc. commercial channels.

**For additional defence-in-depth:**

1. Set `registry.conda_channel: conda-forge` + `conda_channel_strict: true`
   in the YAML config -- all deps are explicitly prefixed with
   `conda-forge::` at `log_model()` time for auditability.
2. Optionally create a restrictive build EAI that only permits outbound
   access to the Artifactory conda proxy (or directly to
   `conda.anaconda.org` for conda-forge). Pass it as
   `build_external_access_integrations` on `create_service()`.
3. Together these provide defence-in-depth: spec-level enforcement
   (channel prefix) + network-level enforcement (build EAI).

**snowflakeR support:** `sfr_deploy_model()` already accepts
`build_external_access_integrations` and passes it through to
`create_service()`:

```r
sfr_deploy_model(
  reg, model_name, version_name,
  service_name   = "MY_SERVICE",
  compute_pool   = "MY_POOL",
  image_repo     = "MY_DB.MY_SCHEMA.MY_REPO",
  build_external_access_integrations = c("APPROVED_REPOS_ONLY_EAI")
)
```

### Warehouse models (if used for Python models)

1. Same `registry.conda_channel` config prefixes conda deps with
   `conda-forge::`.
2. Use the Artifact Repository (`artifact_repository_map`) for pip deps
   to source from public PyPI instead of the Anaconda channel. (Private
   Artifactory PyPI is not yet supported.)
3. Create a Packages Policy (`CREATE PACKAGES POLICY`) with an explicit
   allowlist of approved packages and versions at the account level.
4. If the organisation's "no Anaconda" policy extends to the
   Snowflake-licensed Anaconda Channel, ensure all conda deps carry
   `conda-forge::` and consider disabling `ENABLE_ANACONDA_PACKAGES` at
   the account level (note: this also disables Anaconda for
   UDFs/Sprocs).

### Dependency tree depth: why package count matters

A single top-level R package can pull a deep tree of transitive
dependencies -- both R packages and native C/C++ system libraries. This
is important for capacity planning (Artifactory cache size, first-build
latency) and for compliance auditing (every package in the tree must
come from an approved source).

**Example: `r-tidyverse`**

`r-tidyverse` is a meta-package that declares ~30 direct R dependencies:

```
r-tidyverse
 ├── r-ggplot2, r-dplyr, r-tidyr, r-readr, r-purrr, r-tibble
 ├── r-stringr, r-forcats, r-lubridate, r-haven, r-readxl
 ├── r-xml2, r-httr, r-rvest, r-jsonlite, r-reprex
 ├── r-dbplyr, r-dtplyr, r-googledrive, r-googlesheets4
 ├── r-modelr, r-broom, r-ragg, r-conflicted
 └── r-cli, r-pillar, r-rlang, r-magrittr, r-hms
```

Each of those pulls its own dependencies. For example:

```
r-ggplot2 → r-cli, r-glue, r-gtable, r-isoband, r-lifecycle,
             r-mass, r-mgcv, r-rlang, r-scales, r-tibble,
             r-vctrs, r-withr

r-dplyr   → r-ellipsis, r-generics, r-glue, r-lifecycle,
             r-magrittr, r-pillar, r-r6, r-rlang, r-tibble,
             r-tidyselect, r-vctrs, libcxx

r-readr   → r-cli, r-clipr, r-cpp11, r-crayon, r-hms,
             r-lifecycle, r-r6, r-rlang, r-tibble, r-tzdb,
             r-vroom

r-haven   → r-cli, r-cpp11, r-forcats, r-hms, r-lifecycle,
             r-readr, r-rlang, r-tibble, r-tidyselect,
             r-vctrs, libzlib

r-xml2    → r-cli, r-rlang, libxml2

r-httr    → r-curl, r-jsonlite, r-mime, r-openssl, r-r6
             └── r-curl   → libcurl
             └── r-openssl → openssl (libssl, libcrypto)

r-ragg    → r-systemfonts, r-textshaping, libfreetype,
             libjpeg-turbo, libpng, libtiff

r-rvest   → r-cli, r-glue, r-httr, r-lifecycle, r-magrittr,
             r-rlang, r-selectr, r-tibble, r-withr, r-xml2
```

After deduplication, the full resolved environment typically contains
**80--120 unique conda packages**, including:

| Layer | Examples | Typical count |
|---|---|---|
| **R runtime** | `r-base`, `r-recommended` | ~2 |
| **R packages** (tidyverse tree) | `r-ggplot2`, `r-dplyr`, `r-tibble`, `r-vctrs`, ... | ~60--80 |
| **Native C/C++ libraries** | `libcurl`, `openssl`, `libxml2`, `libzlib`, `libtiff`, `libpng`, `libjpeg-turbo`, `libfreetype`, `libiconv`, `icu`, `libcxx` | ~15--25 |
| **Model inference** | `rpy2`, `cffi`, `pycparser`, `jinja2`, ... | ~10--15 |

**Why this matters:**

- **Artifactory first-build latency** -- On first MODEL_BUILD (cold
  Artifactory cache), all ~100 packages are fetched from upstream
  conda-forge through the proxy. Subsequent builds for the same
  versions hit the Artifactory cache and are fast.
- **Packages Policy auditing** -- Account admins authoring a Packages
  Policy allowlist need to include the transitive closure, not just
  the top-level packages the user explicitly requested.
- **Compliance scope** -- Every package in the tree is downloaded from
  the channel (conda-forge or Artifactory proxy). There are no
  "hidden" packages that bypass the configured source.
- **Cache sizing** -- A single R model environment is roughly
  500 MB--1 GB of cached conda packages. Plan Artifactory storage
  accordingly if hosting multiple model versions.

### Private Artifact Repository for SPCS MODEL_BUILD (in development)

**Status (April 2026):** Snowflake has indicated that private artifact
repository integration for SPCS served models is in active development.
When generally available, it should close the remaining gap for
organisations that require every package download to route through a
corporate proxy—confirm timelines and scope in current release notes.

**What this would enable:**

MODEL_BUILD could resolve conda and pip packages through your
Artifactory instance (or equivalent) instead of going direct to
public conda-forge / PyPI. Combined with the existing
`build_external_access_integrations` for network-level control, that
would provide full end-to-end enforcement.

**Design goals under discussion (check current Snowflake release notes):**

| Requirement | Why it matters |
|---|---|
| **Conda support, not just pip** | R models rely on conda packages (`r-base`, `rpy2`, R package wrappers). If private repo only covers pip, the conda side -- the primary concern -- remains unresolved. |
| **Authenticated access via Snowflake Secrets** | Regulated organisations disable anonymous access on Artifactory. MODEL_BUILD needs to authenticate with credentials stored in a Snowflake Secret (`PASSWORD` type: username + API key). |
| **Conda channel URL remapping** | Currently `conda-forge::pkg` resolves against `conda.anaconda.org/conda-forge`. MODEL_BUILD needs to resolve `conda-forge` through the Artifactory conda remote repo URL instead. |
| **Custom CA / TLS support** | Environments with TLS inspection require MODEL_BUILD to trust the corporate CA bundle when connecting to Artifactory. |
| **Both conda and pip in same build** | MODEL_BUILD can have both `conda_dependencies` and `pip_requirements`, potentially pointing to different Artifactory virtual repos (conda remote + PyPI remote). |
| **Interaction with `channel::` prefix** | Clarify whether `conda-forge::pkg` prefixes are still needed, or whether the private repo becomes the default resolution path (making prefixes unnecessary but harmless). |

**Gotchas to watch for:**

- **First-access latency** -- Artifactory remote repos cache on first
  download. For an R model using `r-tidyverse`, the first MODEL_BUILD
  pulls **80--120 packages** through the proxy (see "Dependency tree
  depth" above). This initial build will be significantly slower while
  Artifactory fetches and caches from upstream. Subsequent builds
  for the same versions hit cache. This could cause timeouts if
  MODEL_BUILD has aggressive time limits.
- **Configuration surface** -- Ideally a parameter on
  `create_service()` (e.g. `build_artifact_repository`), similar to
  the existing `artifact_repository_map` on `log_model()` for
  warehouse models. snowflakeR would expose the same parameter through
  `sfr_deploy_model()` once the underlying SQL/Python API is available.
- **Warehouse models** -- The current Artifact Repository feature for
  warehouse models only supports public PyPI (private repos not
  supported). If the SPCS private repo work is separate, warehouse
  models remain limited to the `channel::` prefix + Packages Policy
  approach.

### Alternative: Bring Your Own Container (BYOC)

An orthogonal direction is **pre-built container images** for model
services: you supply an image reference instead of relying solely on
MODEL_BUILD to assemble the runtime. That would address strict
Artifactory-only provenance and can shorten cold-start time.

**How that pattern typically works**

1. You build the inference image in your own CI/CD pipeline (for example
   Jenkins or GitHub Actions), installing packages only from Artifactory
   so scanning and audit stay in systems you already operate.
2. You push the image to a Snowflake image repository in your account.
3. At `create_service()` time, you reference that image instead of (or
   alongside) a full MODEL_BUILD package-resolution path—subject to
   whatever parameters your Snowflake release exposes.

```python
# Hypothetical API
mv.create_service(
    service_name="MY_SERVICE",
    service_compute_pool="MY_POOL",
    container_image="my_db.my_schema.my_repo/my-r-model:v1.2",
    # MODEL_BUILD skipped -- uses pre-built image directly
)
```

**Benefits:**

| | MODEL_BUILD (current) | BYOC |
|---|---|---|
| **Package source control** | Limited (conda-forge direct, or private repo when available) | Full -- your CI/CD uses Artifactory exclusively |
| **Security scanning** | Packages scanned at download time (if through Artifactory) | Entire image scanned in CI/CD before push |
| **Deployment speed** | 5--20 min (build + package resolution each time) | Seconds (image already built and cached) |
| **Reproducibility** | Package versions resolved at build time; may drift | Exact image pinned; identical across deployments |
| **Compliance audit** | Requires trust in MODEL_BUILD's package resolution | Full CI/CD audit trail in your own systems |

**Faster service instantiation:** This is a significant practical
benefit. MODEL_BUILD currently takes 5--20 minutes per deployment
(longer for GPU models). With a pre-built image, `create_service()`
only needs to pull and start the container -- reducing deployment to
seconds. For organisations that deploy frequently or need rapid
rollbacks, this is a major improvement.

**What Snowflake would need to expose**

- A `container_image` (or similar) parameter on `create_service()`
  that accepts a reference to an image in a Snowflake image repository
- A supported way to skip MODEL_BUILD when a pre-built image is provided
- Clear documentation of the inference container contract (protocol,
  health checks, ports) so you can build compatible images
- snowflakeR would surface the same parameters through `sfr_deploy_model()`
  once the SQL/Python APIs stabilise

**Current status:** Not a supported end-user workflow in the form above;
`create_service()` today centres on MODEL_BUILD. Treat BYOC as a roadmap
item—verify against the latest Model Registry and SPCS documentation.

## Documentation

Companion pages in **this** repository (`docs/`):

- **[custom_mirrors.md](custom_mirrors.md)** — Artifactory/Nexus mirror setup,
  Workspace secrets (`auth_secret`), TLS, troubleshooting
- **[configuration.md](configuration.md)** — YAML reference including `mirrors`
- **[network_rules.md](network_rules.md)** — EAI generation and mirror-aware domains
- **[cli.md](cli.md)** — `sfnb-setup` CLI (automation and container builds)

Examples in those docs use generic hostnames (for example
`artifactory.snowflake.com`) and are not tied to a single organisation.

## Appendix: where this is implemented (open source)

The following paths are for engineers auditing behaviour in the
Snowflake-Labs repositories; you do not need them to *use* mirrors or
`auth_secret` from a notebook.

### Mirrors support (artifact repository proxying)

| File | Change |
|---|---|
| `sfnb_multilang/config.py` | New `MirrorsConfig` dataclass; parsed from YAML `mirrors:` section |
| `sfnb_multilang/installer.py` | Threads mirror URLs through micromamba, conda, and pip install phases |
| `sfnb_multilang/shared/micromamba.py` | Custom URL download with SSL context support |
| `sfnb_multilang/shared/conda_env.py` | `--ssl-verify` flag for custom conda channels |
| `sfnb_multilang/languages/r.py` | CRAN mirror URL for `install.packages()` and `remotes::install_version()` |
| `sfnb_multilang/helpers/sfnb_setup.py` | Mirror-aware EAI domain derivation, pip index flags, tarball SSL |
| `docs/custom_mirrors.md` | New -- comprehensive mirror setup guide |
| `docs/configuration.md` | Updated -- mirrors section reference + cross-links |
| `docs/network_rules.md` | Updated -- mirror-aware EAI simplification section |
| `docs/cli.md` | New -- full CLI reference |
| Example `_config.yaml` files | Added commented `mirrors:` section |

### Registry conda channel enforcement

| File | Change |
|---|---|
| `sfnb_multilang/config.py` | New `RegistryConfig` dataclass; parsed from YAML `registry:` section |
| `sfnb_multilang/helpers/sfnb_setup.py` | New `_apply_registry_env()` exports `SFR_CONDA_CHANNEL` / `SFR_CONDA_CHANNEL_STRICT` env vars from config |
| `snowflakeR/inst/python/sfr_registry_bridge.py` | New `conda_channel` param on `registry_log_model()`; `_prefix_conda_channel()` helper auto-prefixes deps |
| `snowflakeR/R/registry.R` | New `conda_channel` + `conda_channel_strict` on `sfr_model_registry()` and `sfr_log_model()`; strict mode enforcement; env var fallback |
| `docs/configuration.md` | Updated -- `registry` section reference with compliance notes |
| Example `_config.yaml` files | Added commented `registry:` section |
| All derived `sfnb_setup.py` copies | Synced from canonical source |
