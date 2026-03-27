# CLI Reference

The `sfnb-setup` command-line interface provides direct access to the
multi-language toolkit installer, EAI SQL generator, and installation
validator. It is registered as a console entry point when the
`sfnb-multilang` package is installed.

Most users should use `setup_notebook()` from `sfnb_setup.py` -- it
wraps the same installer with automatic EAI discovery, session context
management, and R package installation. The CLI is aimed at power users,
automation pipelines, and container image builds.

## Where to Run

| Context | How | Typical use case |
|---|---|---|
| Workspace Notebook shell cell | `!sfnb-setup install --r` | Ad-hoc install without a config YAML |
| Workspace terminal tab | `sfnb-setup install --config my_config.yaml` | Interactive debugging of install issues |
| Local development | `pip install -e . && sfnb-setup --help` | Dry-run, generate EAI SQL, migrate configs |
| Dockerfile / CI | `RUN sfnb-setup install --r --quiet` | Pre-bake R/Scala/Julia into a custom container image |

The `sfnb-setup` command requires `sfnb-multilang` to be installed
first. In a Workspace Notebook, this happens automatically when you
use `setup_notebook()`. For standalone CLI use:

```bash
pip install sfnb-multilang
sfnb-setup --help
```

## Subcommands

### install

Run the full installation pipeline: preflight checks, micromamba,
conda environment, pip packages, language-specific post-install, and
validation.

```bash
sfnb-setup install [flags]
```

| Flag | Description |
|---|---|
| `--config`, `-c` | Path to a YAML config file |
| `--r` | Enable R |
| `--scala` | Enable Scala/Java |
| `--julia` | Enable Julia |
| `--all` | Enable all languages |
| `--r-adbc` | Enable the R ADBC Snowflake driver addon |
| `--r-duckdb` | Enable the R DuckDB Snowflake extension addon |
| `--apply-eai` | Try to create/update the EAI before installing (default: true) |
| `--no-eai` | Skip EAI setup entirely |
| `--account` | Snowflake account identifier for EAI creation |
| `--verbose`, `-v` | Set log level to DEBUG |
| `--quiet`, `-q` | Suppress INFO messages; show only a final summary line |
| `--force` | Force reinstall even if packages are already present |
| `--dry-run` | Show what would be installed without making changes |

The install pipeline runs eight phases in order:

1. **Phase 0:** Network rule / EAI setup (skipped with `--no-eai`)
2. **Phase 1:** Preflight checks (disk space, PATH, permissions)
3. **Phase 2:** Install or verify micromamba
4. **Phase 3:** Create or update the conda environment
5. **Phase 4:** Install pip packages into the notebook kernel
6. **Phase 5:** Language-specific post-install (CRAN packages, ADBC, DuckDB, symlink fixes)
7. **Phase 6:** Validate each language (R binary, R_HOME, Scala REPL, Julia packages)
8. **Phase 7:** Deploy helper modules to the working directory

### generate-eai

Generate the External Access Integration SQL without installing
anything. Useful for handing the SQL to a Snowflake administrator who
has `ACCOUNTADMIN` privileges.

```bash
sfnb-setup generate-eai [flags]
```

| Flag | Description |
|---|---|
| `--config`, `-c` | Path to a YAML config file |
| `--r` | Include R domains |
| `--scala` | Include Scala/Maven domains |
| `--julia` | Include Julia domains |
| `--all` | Include all language domains |
| `--r-adbc` | Include ADBC addon domains |
| `--account` | Snowflake account identifier (included in SQL comments) |
| `--output`, `-o` | Write SQL to a file instead of stdout |

When `mirrors` are configured in the YAML config, the generated SQL
contains only the mirror host(s) instead of the ~15 public upstream
domains. See [custom_mirrors.md](custom_mirrors.md).

### validate

Check an existing installation without modifying anything. Verifies
that language binaries exist, are executable, and produce expected
output.

```bash
sfnb-setup validate [flags]
```

| Flag | Description |
|---|---|
| `--config`, `-c` | Path to a YAML config file |

Returns exit code 0 if all enabled languages pass validation, 1 if
any fail. Each language check is printed with OK or FAILED status.

### migrate-config

Convert legacy per-language YAML config files into a single unified
config. This is a one-time migration helper for projects that used
earlier versions of the toolkit with separate R, Scala, and Julia
config files.

```bash
sfnb-setup migrate-config [flags]
```

| Flag | Description |
|---|---|
| `--r-config` | Path to legacy R packages YAML |
| `--scala-config` | Path to legacy Scala packages YAML |
| `--julia-config` | Path to legacy Julia packages YAML |
| `--output`, `-o` | Output file path (default: `config.yaml`) |

## Common Flags

These flags are shared across the `install` and `generate-eai`
subcommands:

| Flag | Effect |
|---|---|
| `--config`, `-c` | Load settings from a YAML config file. CLI flags override YAML values. |
| `--r` | Enable R (`languages.r.enabled: true`) |
| `--scala` | Enable Scala/Java (`languages.scala.enabled: true`) |
| `--julia` | Enable Julia (`languages.julia.enabled: true`) |
| `--all` | Enable all three languages |

When `--config` is provided, the YAML file is loaded first and then
CLI flags are applied on top. This means you can use a base config
file and selectively override settings from the command line:

```bash
sfnb-setup install --config base_config.yaml --r-adbc --verbose
```

## Examples

### Preview what would be installed (dry-run)

```bash
sfnb-setup install --config my_config.yaml --dry-run
```

Output:

```
Dry run -- would install:
  Languages: R
  Conda packages: r-base=4.5.2 r-tidyverse r-dbplyr r-reticulate>=1.25
  Pip packages: nevergrad
```

### Generate EAI SQL for an administrator

```bash
sfnb-setup generate-eai --r --r-adbc --account MYORG-MYACCOUNT -o eai_setup.sql
```

Hand `eai_setup.sql` to your Snowflake admin. They run it with
`ACCOUNTADMIN`, then you enable the EAI on your notebook:
**Connected > Edit > External Access > toggle on > Save**.

### Install R with ADBC in quiet mode

```bash
sfnb-setup install --r --r-adbc --quiet
```

Produces a single summary line on success:

```
Installation complete in 142.3s -- R: OK (R version 4.5.2)
```

### Validate after a manual environment change

```bash
sfnb-setup validate --config my_config.yaml
```

```
  R: OK
  Scala: FAILED
    Scala REPL not found at /home/jupyter/micromamba/envs/workspace_env/bin/amm
```

### Migrate from legacy per-language configs

```bash
sfnb-setup migrate-config \
  --r-config r_packages.yaml \
  --scala-config scala_packages.yaml \
  --output config.yaml
```

### Install in a Dockerfile (container pre-bake)

```dockerfile
FROM snowflake-notebook-base:latest
RUN pip install sfnb-multilang
COPY config.yaml .
RUN sfnb-setup install --config config.yaml --no-eai --quiet
```

The `--no-eai` flag skips EAI setup since there is no Snowpark session
during a Docker build. The EAI is configured separately when the
container runs as a Workspace Notebook service.

## CLI vs setup_notebook()

| Capability | `setup_notebook()` | `sfnb-setup` CLI |
|---|---|---|
| **Primary audience** | Notebook users | Power users, CI/CD, admins |
| **Where it runs** | Workspace Notebook Python cell | Any shell with `sfnb-multilang` installed |
| **EAI discovery** | Multi-tier auto-discovery (DESC SERVICE, settings.json, SHOW INTEGRATIONS) | Phase 0 only (CREATE/ALTER via session, or export SQL) |
| **Session context** | Reads `context:` from YAML, sets USE DATABASE/SCHEMA/WAREHOUSE | No session awareness |
| **R package tarballs** | Installs from URL, local path, or glob search with GitHub fallback | Not handled (conda + CRAN packages only) |
| **R package pip deps** | Installs `pip_packages` from config (e.g. nevergrad) | Installs pip packages from config |
| **ML import pre-warm** | Pre-imports `snowflake.ml` to avoid cold-start latency | Not handled |
| **SPCS OAuth env vars** | Sets `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, etc. for RSnowflake | Not handled |
| **Dry-run** | Not available | `--dry-run` flag |
| **EAI SQL export** | Prints SQL on privilege failure | `generate-eai` subcommand |
| **Config migration** | Not available | `migrate-config` subcommand |
| **Custom mirrors** | Reads `mirrors:` from YAML | Reads `mirrors:` from YAML (via `--config`) |

In short: `setup_notebook()` is the batteries-included path for
interactive notebook use. The CLI provides lower-level control for
automation, debugging, and environments where a Snowpark session is
not available.
