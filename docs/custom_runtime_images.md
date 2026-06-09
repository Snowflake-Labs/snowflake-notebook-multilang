# Custom Runtime Images for Faster Notebook Bootstrap

[Snowflake Custom Runtime Images](https://docs.snowflake.com/en/developer-guide/snowflake-ml/custom-runtime-images)
(Public Preview) let Workspace Notebooks run on container images you build and
register. This toolkit ships a **reference CRE** that pre-installs micromamba,
R, snowflakeR/RSnowflake, and `%%R` auto-registration so analysts skip the
**~60s typical** `setup_notebook()` per compute restart (standard config; scales up with extra R packages).

**Typical enterprise flow:** platform or IT runs a **CRE onboarding** (build image, register in Snowflake, grant roles); analysts then attach `cre@<org_name>` in Workspace.

**Bootstrap** (`setup_notebook()`) is for **pilots and individuals** before that org image exists, or when Docker/CRE is not approved yet.

See [CRE vs bootstrap](#cre-vs-bootstrap) and [Organisation operating model](org_cre_operating_model.md).

## CRE vs bootstrap {#cre-vs-bootstrap}

| | **Org CRE (IT/platform onboarding)** | **Self-serve bootstrap** |
|---|--------------------------------------|--------------------------|
| **Who sets it up** | Platform / IT | Analyst or pilot team |
| **Best for** | Production after org go-live | PoC before CRE exists, package experiments |
| **First `%%R`** | Seconds after container is up (reference v1 auto-startup) | Minutes (micromamba + tarballs + EAI) |
| **Governance** | Image scanned in CI; pinned digest | Runtime downloads (EAI required) |
| **Empty git repo** | Reference v1: no setup cell | Bootstrap cell required |
| **Maintenance** | Platform team owns image + profile YAML | Each notebook / YAML |

**Caching:** Image **layers** cache on registry/nodes after first pull. **Kernel restart** in the same session is cheap. **Idle container recycle** starts fresh compute: CRE keeps R in the image; bootstrap reinstalls R.

Path matrix (CRE / bootstrap / GPU / local IDE): [cre_path_matrix.md](cre_path_matrix.md).

## Versioned reference images (catalog) {#image-catalog}

Publish **tags aligned to snowbooks / Container Runtime**, not ad-hoc `:latest` only.

| Tag | snowbooks / runtime | `%%R` auto | sfnb-multilang | ADBC | Notes |
|-----|---------------------|------------|----------------|------|-------|
| **v1** | 2.5.x CPU | **Yes** | **Yes** | **Yes** | **First supported reference (CPU)** |
| v1-gpu | 2.5.x **GPU** | Yes | Yes | Optional | Separate image + `BASE_IMAGE_TYPE = GPU` |

Earlier internal image experiments were not published as a separate version. If your registry still has `:v2` from development, it is the same recipe as **v1** — rebuild and tag `:v1` for new rollouts.

Build CPU and GPU images as **separate CRE objects** (`sfnb_multilang_r`, `sfnb_multilang_r_gpu`).
Customers rebuild from this repo with `docker/create_cre.sh` and their own registry path —
there is no shared public pull URL (CRE images live in **your account's** image repository).

## What gets pre-baked (reference v1)

| Component | Default location | Notes |
|-----------|------------------|-------|
| micromamba | `~/micromamba` | Same path as default YAML config |
| Conda env `workspace_env` | micromamba env prefix | R 4.5.2 + tidyverse/dbplyr/DBI stack |
| snowflakeR + RSnowflake | R library in env | GitHub release tarballs at **image build** time |
| **ADBC (v1)** | R library + conda | `adbcdrivermanager`, `adbcsnowflake`, … |
| rpy2, tabulate | Notebook Python | pip at image build |
| **sfnb-multilang** | Notebook Python | `setup_notebook`, `enable_r_cells`, … |
| **`%%R` auto-registration** | `~/.ipython/.../startup/` | Kernel startup hook |
| `cre_multilang_r.yaml` | `/opt/sfnb/config/` | Optional `setup_notebook()` preset |

The image **extends** the official **snowbooks** ML runtime base (required for CRE validation).

## Build location: local Docker vs in-account Image Builder

| Approach | When it helps |
|----------|----------------|
| **Local `docker build` + `docker push`** | Familiar CI; works everywhere Snowflake CLI can log in to the registry |
| **SPCS Image Builder** (`snow spcs service build-image`, preview) | Build runs **inside your Snowflake account** — often faster **upload** when your build machine is far from the account region |

Geography does not change the Dockerfile; it changes **where BuildKit runs** and how long **push** takes. Many teams use **local build** for iteration and **in-account build** for production pipelines once Image Builder is enabled.

### SPCS Image Builder (optional, preview)

**Not GA** — requires SnowCLI 3.16+ and a feature flag:

```toml
# ~/.snowflake/config.toml
[cli.features]
enable_spcs_build_image = true
```

**Prerequisites:** compute pool, image repository with **OWNERSHIP**, EAI for egress during build.

**Flat context:** Image Builder preview requires a **single-directory** context. Use `docker/prepare_build_ctx.sh`, then `docker/build_cre_snowflake.sh` with **your** connection and repo names (all via environment variables — no account-specific defaults in the script).

**Known limitations (preview):**

- Ephemeral build disk may be too small for full `snowbooks` extract — if builds fail with `no space left on device`, use **local Docker** until Snowflake increases builder disk or supports layer-mount `FROM`.
- `FROM` in the flat Dockerfile must use your account's **`registry-local.snowflakecomputing.com`** host for in-account builds (see `docker/build-ctx/Dockerfile` and `REGISTRY_LOCAL_URL` build arg).

```bash
export SNOW_CONNECTION=your_snow_cli_connection
export SNOW_DATABASE=MYDB
export SNOW_SCHEMA=MYSCHEMA
export SFNB_IMAGE_REPO=MYDB.MYSCHEMA.MY_IMAGE_REPO
export SFNB_BUILD_POOL=MY_BUILD_POOL
export SFNB_BUILD_EAI=MY_BUILD_EAI
export REGISTRY_LOCAL_URL=yourorg-youracct.registry-local.snowflakecomputing.com
./docker/build_cre_snowflake.sh
```

## Profile-driven build (`create_cre.sh`) — recommended

```bash
./docker/create_cre.sh --init my_org
# edit configs/my_org.yaml from configs/cre_profile.example.yaml
./docker/create_cre.sh configs/my_org.yaml
PUSH=1 SNOW_CONNECTION=your_connection ./docker/create_cre.sh configs/my_org.yaml
```

Outputs: `docker/generated/cre_extra_install.sh`, `cre_profile.env`, `cre_register.sql`.

**Do not commit** `configs/cre_profile.yaml` with real registry hosts if your policy forbids it; use the example template only in git.

## Quick workflow (local Docker)

### 1. Authenticate Docker to Snowflake registry

```bash
snow spcs image-registry login -c your_connection   # optional -c
export REGISTRY_URL="yourorg-youracct.registry.snowflakecomputing.com"
```

Find the host in Snowsight (**Admin → Image repositories**) or `SHOW IMAGE REPOSITORIES`.

### 2. Build and validate

```bash
cd snowflake-notebook-multilang
export REGISTRY_URL="yourorg-youracct.registry.snowflakecomputing.com"
./docker/build_cre.sh
# or: ./docker/create_cre.sh configs/my_org.yaml
```

Uses `docker build --platform linux/amd64` and `snow custom-image validate` when the CLI supports it.

### 3. Push

```bash
export IMAGE_REPO_PATH="mydb/myschema/my_repo"   # lowercase path
PUSH=1 ./docker/build_cre.sh
```

### 4. Register CRE

```sql
CREATE OR REPLACE CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_r
    IMAGE_PATH = '/mydb/myschema/my_repo/sfnb-multilang-r:v1'
    BASE_IMAGE_TYPE = CPU;

GRANT USAGE ON CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_r TO ROLE <notebook_role>;
```

Use `BASE_IMAGE_TYPE = GPU` only for images built from the **GPU** snowbooks base.

### 5. Attach in Workspace

**Snowsight:** Notebook → **Advanced settings** → your CRE.

**SQL:**

```sql
EXECUTE NOTEBOOK PROJECT MYDB.MYSCHEMA.MY_PROJECT
    MAIN_FILE = 'notebook.ipynb'
    COMPUTE_POOL = 'SYSTEM_COMPUTE_POOL_CPU'
    RUNTIME = 'cre@sfnb_multilang_r'
    QUERY_WAREHOUSE = 'MY_WH';
```

Notebook template (no bootstrap cell on reference v1): [examples/cre_workspace_notebook_template.md](../examples/cre_workspace_notebook_template.md).

### 6. Verify

```r
%%R
cat(R.version.string, "\n")
packageVersion("snowflakeR")
```

Optional context-only bootstrap:

```python
from sfnb_setup import setup_notebook
setup_notebook(config="/opt/sfnb/config/cre_multilang_r.yaml", packages=["snowflakeR", "RSnowflake"])
```

## Expected startup times

| Scenario | Typical experience |
|----------|-------------------|
| Default runtime (cold bootstrap) | ~60s typical per restart; longer with extra packages |
| Default runtime (warm micromamba cache) | ~2s + bootstrap logic |
| **Reference CRE v1** | **0 setup cells**; `%%R` on kernel start |
| Cold bootstrap only | Bootstrap cell required for `%%R` |

## Customizing the image

- **Profile YAML:** `extras.conda_r`, `extras.cran`, `extras.pip` — see `configs/cre_profile.example.yaml`
- **Advanced:** edit `docker/install_prebaked_r.sh`, bump tag, rebuild, `CREATE OR REPLACE` CRE

GPU R packages (`torch`, etc.): use a **GPU snowbooks** base and [Hitchhiker's Guide Appendix G (GPU)](https://snowflake-labs.github.io/snowflakeR/appendices/G_workspace_container_internals/index.html#sec-gpu) — align CUDA with [GPU Container Runtime 2.5](https://docs.snowflake.com/en/developer-guide/snowflake-ml/container-runtime/releases/gpu/2_5).

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| CRE `CREATE` fails | Not derived from snowbooks — run `snow custom-image validate` |
| `exec format error` | Build without `--platform linux/amd64` |
| Still slow | Notebook not using CRE — check advanced settings / `RUNTIME` |
| `%%R` unknown on CRE | Wrong image tag or CRE not recreated after push |
| renv hang | `.Rprofile` on `/filesystem` git mount — use reference CRE v1 or `enable_r_cells()` |
| Digest rejected at run | Image changed after register — `CREATE OR REPLACE` CRE |

Container internals: see the Hitchhiker's Guide **Appendix G** (Workspace container internals).

## References

- [Organisation operating model](org_cre_operating_model.md)
- [Path matrix: CRE vs bootstrap vs GPU](cre_path_matrix.md)
- [Configuration reference](configuration.md)
- [Quick start](quickstart.md)
- [Snowflake: Custom Runtime Images](https://docs.snowflake.com/en/developer-guide/snowflake-ml/custom-runtime-images)
