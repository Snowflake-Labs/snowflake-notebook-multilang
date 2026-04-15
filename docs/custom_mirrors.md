# Custom Package Mirrors (Artifactory / Nexus / Air-Gapped)

Organizations in regulated industries (banking, insurance, healthcare,
government) often require that all external packages pass through an
internal artifact repository proxy before entering their network. This
document explains how to configure `sfnb-multilang` to route every
package download through a corporate mirror.

## Background

By default the toolkit downloads packages from public upstream sources:

| Package type | Default source |
|---|---|
| conda (R runtime, pre-compiled R packages) | `conda.anaconda.org/conda-forge` |
| pip (sfnb-multilang, nevergrad, ipywidgets) | `pypi.org` |
| CRAN (R packages not on conda-forge) | `cloud.r-project.org` |
| R tarballs (snowflakeR, RSnowflake) | `github.com` release assets |
| micromamba binary | `micro.mamba.pm` / GitHub Releases |

In a locked-down environment these public domains are blocked. An
artifact repository proxy sits between the Snowflake Notebook and the
internet, caching, scanning, and auditing every artifact.

## Supported Repository Managers

The `mirrors` configuration is **tool-agnostic**. It works with any
artifact repository that speaks the standard protocol for each package
manager:

| Tool | Conda | PyPI | CRAN | Generic |
|---|:---:|:---:|:---:|:---:|
| **JFrog Artifactory** (Pro/Enterprise) | Yes | Yes | Yes | Yes |
| **Sonatype Nexus** (OSS or Pro) | Yes | Yes | Yes | Yes |
| **AWS CodeArtifact** | No | Yes | No | No |
| **Azure Artifacts** | No | Yes | No | No |
| **GitLab Package Registry** | No | Yes | No | No |
| **GitHub Packages** | No | Yes | No | No |

For full coverage of all five package types (conda, PyPI, CRAN, R
tarballs, micromamba), **Artifactory Pro/Enterprise** or **Nexus** are
recommended. Tools that lack Conda and CRAN support require alternative
approaches (see [Partial Mirror Coverage](#partial-mirror-coverage)).

## Configuration

Add a `mirrors` section to your `_config.yaml`:

```yaml
mirrors:
  conda_channel: "https://artifactory.snowflake.com/conda-forge-remote"
  pypi_index: "https://artifactory.snowflake.com/api/pypi/pypi-remote/simple"
  cran_mirror: "https://artifactory.snowflake.com/cran-remote"
  micromamba_url: "https://artifactory.snowflake.com/generic-tools/micromamba/linux-64/latest"
  ssl_cert_path: "/etc/ssl/certs/corporate-ca-bundle.crt"
```

All fields are optional. Omitted fields fall back to the public default.

### Field Reference

| Field | Protocol | Default | Description |
|---|---|---|---|
| `conda_channel` | Conda channel | `conda-forge` | URL to a Conda remote/virtual repo that proxies `conda-forge` |
| `pypi_index` | PEP 503 Simple API | `https://pypi.org/simple` | URL to a PyPI remote repo |
| `cran_mirror` | CRAN repo | `https://cloud.r-project.org` | URL to a CRAN remote repo |
| `micromamba_url` | HTTP(S) binary download | `micro.mamba.pm` / GitHub | Direct URL to a micromamba binary or `.tar.bz2` archive |
| `ssl_cert_path` | N/A | system default | Path to a CA certificate bundle for TLS inspection proxies |
| `auth_secret` | N/A | (none) | Fully qualified name of a Snowflake `PASSWORD` secret for mirror authentication (see [Authenticated Mirrors](#authenticated-mirrors)) |

### Tarball URLs

R packages distributed as GitHub release tarballs (snowflakeR,
RSnowflake) are configured separately in the `tarballs` section.
Point these at your artifact repository's generic repo:

```yaml
languages:
  r:
    tarballs:
      snowflakeR: "https://artifactory.snowflake.com/generic-r/snowflakeR_0.1.0.tar.gz"
      RSnowflake: "https://artifactory.snowflake.com/generic-r/RSnowflake_0.2.0.tar.gz"
```

## Complete Example

A complete config for a fully mirrored environment:

```yaml
env_name: "workspace_env"

mirrors:
  conda_channel: "https://artifactory.snowflake.com/conda-forge-remote"
  pypi_index: "https://artifactory.snowflake.com/api/pypi/pypi-remote/simple"
  cran_mirror: "https://artifactory.snowflake.com/cran-remote"
  micromamba_url: "https://artifactory.snowflake.com/generic-tools/micromamba/linux-64/latest"
  ssl_cert_path: "/etc/ssl/certs/corporate-ca-bundle.crt"

languages:
  r:
    enabled: true
    r_version: "4.5.2"
    conda_packages:
      - r-tidyverse
      - r-dbplyr
      - r-reticulate>=1.25
    cran_packages:
      - lares
      - Robyn
    pip_packages:
      - nevergrad
    tarballs:
      snowflakeR: "https://artifactory.snowflake.com/generic-r/snowflakeR_0.1.0.tar.gz"
      RSnowflake: "https://artifactory.snowflake.com/generic-r/RSnowflake_0.2.0.tar.gz"
```

## EAI Simplification

When mirrors are configured, the EAI domain list is automatically
reduced to just the mirror host(s). Instead of the standard ~15 public
domains, the generated EAI SQL contains only your artifact repository:

```sql
CREATE OR REPLACE NETWORK RULE MULTILANG_NOTEBOOK_EGRESS
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = (
    'artifactory.snowflake.com'
  );

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION MULTILANG_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (MULTILANG_NOTEBOOK_EGRESS)
  ENABLED = TRUE;
```

This is a significant security improvement for regulated environments:
one auditable endpoint instead of a dozen public domains.

## Repository Setup Guide

### What to Create

Your artifact repository admin needs to create these repositories:

| Repository | Type | Upstream | Contents |
|---|---|---|---|
| `conda-forge-remote` | Conda Remote | `https://conda.anaconda.org/conda-forge` | Auto-caching proxy; no manual uploads |
| `pypi-remote` | PyPI Remote | `https://pypi.org` | Auto-caching proxy; no manual uploads |
| `cran-remote` | CRAN Remote | `https://cloud.r-project.org` | Auto-caching proxy; no manual uploads |
| `generic-r` | Generic (local) | N/A | Manual upload: `snowflakeR_*.tar.gz`, `RSnowflake_*.tar.gz` |
| `generic-tools` | Generic (local) | N/A | Manual upload: `micromamba` binary |

**Remote repositories** are auto-caching proxies. On the first request
for a package, the repository fetches it from upstream, caches it
locally, and serves it. Subsequent requests are served from cache. The
security team can scan cached artifacts before allowing them into
production.

**Generic (local) repositories** require manual artifact uploads. These
hold the four artifacts we distribute as direct URL downloads rather
than through a standard package manager:

| Artifact | Download from | Upload to |
|---|---|---|
| `snowflakeR_0.1.0.tar.gz` | [GitHub Releases](https://github.com/Snowflake-Labs/snowflakeR/releases) | `generic-r/` |
| `RSnowflake_0.2.0.tar.gz` | [GitHub Releases](https://github.com/Snowflake-Labs/RSnowflake/releases) | `generic-r/` |
| `micromamba` (linux-64) | [micro.mamba.pm](https://micro.mamba.pm/api/micromamba/linux-64/latest) | `generic-tools/micromamba/linux-64/latest` |

### Artifactory-Specific Setup

```bash
# Create remote repos (Artifactory CLI)
jf rt repo-create conda-forge-remote --repo-type remote \
  --package-type conda --url https://conda.anaconda.org/conda-forge

jf rt repo-create pypi-remote --repo-type remote \
  --package-type pypi --url https://pypi.org

jf rt repo-create cran-remote --repo-type remote \
  --package-type cran --url https://cloud.r-project.org

# Create generic local repos
jf rt repo-create generic-r --repo-type local --package-type generic
jf rt repo-create generic-tools --repo-type local --package-type generic

# Upload artifacts
jf rt upload snowflakeR_0.1.0.tar.gz generic-r/
jf rt upload RSnowflake_0.2.0.tar.gz generic-r/
jf rt upload micromamba generic-tools/micromamba/linux-64/latest
```

### Nexus-Specific Setup

In the Nexus admin UI:

1. **Repositories > Create repository**
2. Choose `conda (proxy)`, set Remote URL to `https://conda.anaconda.org/conda-forge`
3. Repeat for `pypi (proxy)` -> `https://pypi.org` and `r (proxy)` -> `https://cloud.r-project.org`
4. Create `raw (hosted)` repositories for `generic-r` and `generic-tools`
5. Upload artifacts via the Nexus UI or `curl`

## TLS Inspection (ssl_cert_path)

Banks and regulated organizations commonly use TLS inspection (also
called SSL interception or MITM proxying) for Data Loss Prevention.
Corporate proxies terminate and re-encrypt TLS traffic using an
internal CA certificate. Standard CA bundles do not include this
internal CA, so HTTPS requests from the Notebook container will fail
with certificate verification errors.

Set `ssl_cert_path` to your organization's CA bundle:

```yaml
mirrors:
  ssl_cert_path: "/etc/ssl/certs/corporate-ca-bundle.crt"
```

### Workspace Notebooks and CA bundles

Workspace notebooks run in a **Snowflake-managed** container. Treat
`ssl_cert_path` as any **POSIX path that exists in your session** when
setup runs (the helper checks `os.path.isfile`). You generally **cannot**
rely on baking files into `/etc/ssl/certs/` on the base image yourself.

Practical options:

- **Git-backed Workspace** — commit the corporate CA PEM in the
  repository mounted for the notebook (for example `certs/corp-root.pem`)
  and set `ssl_cert_path` to that mounted path.
- **Upload or materialise a file** — add the PEM via Workspace/Snowsight,
  or run a bootstrap cell before `setup_notebook()` to copy it from a
  Snowflake stage to a path such as `/tmp/corp-ca.pem`, then reference
  that path in YAML.

The `/etc/ssl/certs/...` examples below are **illustrative Linux paths**;
under Workspace they are only valid if that file truly exists there.

### How enterprises usually supply the CA

The corporate **root or inspection CA is almost never a secret** in the
same way as a password (it is public-key material), but your security team
still controls **where** it is published and whether it may leave the
network. Typical patterns:

| Source | What it looks like | In Workspace |
|---|---|---|
| **Git (most common for notebooks)** | PKI or platform commits `certs/corp-root.pem` (or a merged bundle) into the **same private Git repo** that backs the Workspace. Rotation is a PR + redeploy. | Path is stable under the repo mount; point `ssl_cert_path` there. No extra egress. |
| **Snowflake internal stage** | SecOps runs automation (`PUT`) to `@PROD_SECOPS.PKI.CORP_CA/root.pem`; RBAC grants `READ` to notebook roles. | First cell uses Snowpark / SQL to **download** the file to `/tmp/corp-ca.pem`, then YAML uses that path. EAI must allow any HTTPS host you call to fetch it, if you use HTTP at all. |
| **Internal HTTPS “well-known” URL** | Many firms host the PEM at `https://pki.corp.example/ca.pem` or similar on the corporate intranet. | A bootstrap cell can `curl` or `urllib.request` **if** the notebook EAI allows that hostname and (when required) client auth is solvable. This is workable but more moving parts than Git or a stage. |
| **Laptop → upload** | Analyst obtains the PEM from the intranet, uploads through Workspace/Snowsight if your UI supports it. | Operational; fine for pilots, brittle at scale. |

**Order of operations:** whatever you choose, the PEM must **exist on
disk before** `setup_notebook()` reads the YAML. A common pattern is
**cell 1** (materialise cert → `/tmp/...` or confirm Git path) and
**cell 2** `setup_notebook(config=...)`. Use the **same path** for
`CURL_CA_BUNDLE` in cell 1 or 2 if R needs it.

There is usually **no** “public global filesystem” for your company CA;
it lives on **your** intranet, **your** Git default branch, or **your**
Snowflake stage—then you **copy** it into the ephemeral Workspace file
system for the session.

This certificate is used by:
- **micromamba** (`--ssl-verify` flag) for conda package downloads
- **pip** (`--cert` flag) for PyPI package downloads
- **urllib** (Python `ssl.create_default_context`) for tarball downloads

For CRAN packages, R uses its own SSL stack. If your CRAN mirror also
requires a custom CA certificate, set the R environment variable in a
setup cell **before** calling `setup_notebook()`, using the **same path**
as `ssl_cert_path`:

```python
import os
os.environ["CURL_CA_BUNDLE"] = "/tmp/corp-ca.pem"  # example: match ssl_cert_path
```

## Authenticated Mirrors

Most regulated organizations disable anonymous access on their
artifact repository (JFrog's recommended security posture). When
anonymous access is off, every client must authenticate -- typically
with a username and API key.

The `auth_secret` field in the `mirrors` config references a
**Snowflake SECRET** (type `PASSWORD`) that stores the repository
credentials. `setup_notebook()` reads the secret at runtime and
injects basic-auth credentials into all mirror URLs automatically.
No credentials appear in the YAML config.

### Prerequisites

Snowflake Secrets in Workspace Notebooks (private preview, April
2026). Requires:

- Snowpark Secrets API (`snowflake.snowpark.secrets`) or container
  mount path access (`/secrets/...`)
- A `PASSWORD`-type Snowflake Secret
- The secret included in the EAI via `ALLOWED_AUTHENTICATION_SECRETS`

### Setup

**1. Create the secret (admin, one-time):**

```sql
CREATE SECRET mydb.myschema.artifactory_creds
  TYPE = PASSWORD
  USERNAME = 'deploy-token'
  PASSWORD = '<artifactory-api-key>';
```

The username is typically a service account or deploy token. The
password is the Artifactory API key or identity token.

**2. Include the secret in the EAI:**

```sql
CREATE OR REPLACE NETWORK RULE MULTILANG_NOTEBOOK_EGRESS
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = ('artifactory.example.com');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION MULTILANG_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (MULTILANG_NOTEBOOK_EGRESS)
  ALLOWED_AUTHENTICATION_SECRETS = (mydb.myschema.artifactory_creds)
  ENABLED = TRUE;
```

When `auth_secret` is configured, the toolkit's EAI SQL generator
automatically includes `ALLOWED_AUTHENTICATION_SECRETS` in the
generated SQL.

**3. Select the secret when creating the notebook service:**

In Snowsight, when creating or editing the notebook service, add the
secret (`mydb.myschema.artifactory_creds`) in the Secrets field of
the service creation dialog.

**4. Add `auth_secret` to the YAML config:**

```yaml
mirrors:
  conda_channel: "https://artifactory.example.com/conda-forge-remote"
  pypi_index: "https://artifactory.example.com/api/pypi/pypi-remote/simple"
  cran_mirror: "https://artifactory.example.com/cran-remote"
  micromamba_url: "https://artifactory.example.com/generic-tools/micromamba/linux-64/latest"
  ssl_cert_path: "/etc/ssl/certs/corporate-ca-bundle.crt"
  auth_secret: "mydb.myschema.artifactory_creds"
```

The `auth_secret` value is the fully qualified name of the Snowflake
Secret (`database.schema.name`). Both dot notation
(`mydb.myschema.artifactory_creds`) and slash notation
(`mydb/myschema/artifactory_creds`) are accepted.

### How it Works

When `setup_notebook()` runs:

1. The secret is read via `snowflake.snowpark.secrets.get_username_password()`
   (with a fallback to the container mount path at
   `/secrets/db/schema/name/{username,password}`)
2. The username and password are injected as basic-auth credentials
   into each mirror URL: `https://user:token@host/path`
3. The authenticated URLs are passed to pip (`--index-url`), conda
   (channel URL), micromamba (download URL), and CRAN (`repos`)
4. Credentials are masked in all log output (`user:****@host`)

If the secret cannot be read (e.g. not attached to the service, or
the Secrets API is unavailable), a warning is logged and mirror URLs
remain unauthenticated.

### Security Notes

- Credentials are never written to disk, config files, or log files
- The secret is managed via Snowflake's RBAC -- only roles with
  `USAGE` on the secret can read it
- Credential rotation is handled by updating the Snowflake Secret;
  no YAML changes or notebook restarts needed
- The `PASSWORD` secret type is recommended for Artifactory API keys
  (`USERNAME` = service account, `PASSWORD` = API key)

## Zero-Code-Change Alternative

If modifying the config YAML is not practical, the same result can be
achieved via standard configuration files that each package manager
reads automatically. These go in the Workspace container's home
directory or are set as environment variables.

### .condarc (conda/micromamba)

```yaml
channels:
  - https://artifactory.snowflake.com/conda-forge-remote
default_channels:
  - https://artifactory.snowflake.com/conda-forge-remote
ssl_verify: /etc/ssl/certs/corporate-ca-bundle.crt
```

### pip.conf (pip)

```ini
[global]
index-url = https://artifactory.snowflake.com/api/pypi/pypi-remote/simple
cert = /etc/ssl/certs/corporate-ca-bundle.crt
trusted-host = artifactory.snowflake.com
```

### .Rprofile (CRAN)

```r
options(repos = c(CRAN = "https://artifactory.snowflake.com/cran-remote"))
```

The YAML `mirrors` config is preferred because it is portable, version-
controlled, and visible to the EAI domain generator. The dotfile
approach works but the EAI must be configured manually.

## Partial Mirror Coverage

If your artifact repository does not support all five package types
(e.g. AWS CodeArtifact supports PyPI but not Conda or CRAN), you can
mix mirrored and direct access:

```yaml
mirrors:
  # Only PyPI goes through CodeArtifact
  pypi_index: "https://my-domain-123456789.d.codeartifact.us-east-1.amazonaws.com/pypi/pypi-store/simple/"
  # conda and CRAN still use public defaults (require EAI domains)
```

In this case, the EAI will include both the CodeArtifact domain and
the public conda-forge/CRAN domains. Only fully mirrored package types
have their public domains removed from the EAI.

## Troubleshooting

### "SSL: CERTIFICATE_VERIFY_FAILED"

Your organization uses TLS inspection. Set `ssl_cert_path` to the
corporate CA bundle. Ask your IT team for the path -- common locations:

- `/etc/ssl/certs/ca-certificates.crt` (Debian/Ubuntu)
- `/etc/pki/tls/certs/ca-bundle.crt` (RHEL/CentOS)
- `/etc/ssl/cert.pem` (macOS)

### "Could not find a version that satisfies the requirement"

The PyPI mirror may not have the package cached yet. Verify the mirror
URL is correct and that the mirror has upstream access to `pypi.org`.
Test from a machine with direct access:

```bash
pip install --index-url https://artifactory.snowflake.com/api/pypi/pypi-remote/simple nevergrad
```

### "403 Forbidden" or "Access denied" from mirror

This typically means the package **has** been cached by the mirror but
was **blocked by a security scanning policy** (e.g. Artifactory Xray,
Nexus IQ Server, or a manual approval gate). This is distinct from a
"not found" error -- the artifact exists but your repository's security
policy is preventing download.

Common causes:

- **Vulnerability scan pending:** The artifact was pulled into the
  cache but the async security scan hasn't completed yet. Some
  organizations configure a quarantine period during which artifacts
  are not served. Wait for the scan to complete or ask your IT team
  to check the scan queue.
- **Vulnerability policy violation:** The scan completed and found a
  CVE that exceeds your organization's severity threshold. Your IT
  team needs to either approve an exception or you need to find an
  alternative package version without the flagged vulnerability.
- **License policy violation:** The package's license (e.g. GPL,
  AGPL) is on your organization's blocklist. This requires a license
  exception from your legal/compliance team.
- **Unsigned or unverified artifact:** Some policies block packages
  that lack signatures or provenance metadata.

To diagnose, check the artifact status in your repository manager's
UI (Artifactory: Application > Security & Compliance > Watch Violations;
Nexus: IQ Server > Reports) or ask your IT team to check the block
reason for the specific package.

### "Package not found" from conda

The conda channel URL may be incorrect. Conda remote repos in
Artifactory use the path `/<repo-key>` directly, not the Artifactory
API path. Verify:

```bash
# Correct
conda_channel: "https://artifactory.snowflake.com/conda-forge-remote"

# Wrong (API path)
conda_channel: "https://artifactory.snowflake.com/api/conda/conda-forge-remote"
```

### "401 Unauthorized" from mirror

Anonymous access is disabled on your artifact repository but
`auth_secret` is not configured (or the secret could not be read).

**Diagnosis:**

1. Verify `auth_secret` is set in your config YAML
2. Check that the Snowflake Secret exists:
   `DESCRIBE SECRET mydb.myschema.artifactory_creds;`
3. Verify the secret is included in the EAI:
   `DESCRIBE INTEGRATION MULTILANG_NOTEBOOK_EAI;` -- look for
   `ALLOWED_AUTHENTICATION_SECRETS`
4. Verify the secret was selected when creating the notebook service
5. Check the setup log for `auth_secret ... credentials could not be
   read` -- this means neither the Snowpark API nor the mount path
   could access the secret

### "auth_secret configured but credentials could not be read"

The `auth_secret` is set in the YAML but `setup_notebook()` could
not read the credentials. Common causes:

- **Secret not attached to notebook service:** The secret must be
  selected when creating or editing the Workspace notebook service in
  Snowsight, in addition to SQL on the EAI. Follow current Workspace
  documentation for your account (attach the secret using the fully
  qualified name `DB.SCHEMA.SECRET_NAME` where the UI asks for it). If
  this step is skipped, the Snowpark Secrets API and the `/secrets/...`
  mount both fail, and mirror URLs stay unauthenticated.
- **Secret not in EAI:** The secret must be listed in
  `ALLOWED_AUTHENTICATION_SECRETS` on the EAI
- **Wrong secret path:** The `auth_secret` value must be fully
  qualified (`db.schema.name` or `db/schema/name`)
- **Snowpark Secrets API not available:** If the private preview is
  not enabled for your account, the container mount path fallback
  is used instead

### micromamba download fails

If `micromamba_url` is not set or the URL is unreachable, the toolkit
falls back to the standard download strategies (micro.mamba.pm, GitHub
Releases). If all strategies fail, pre-install micromamba in the
container image or download it manually and place it at
`~/micromamba/bin/micromamba`.
