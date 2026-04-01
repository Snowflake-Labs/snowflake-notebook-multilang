# Configuration Reference

The toolkit is configured via a YAML file. Every section is optional;
omitted sections use sensible defaults.

## Per-Notebook Config (setup_notebook)

When using `setup_notebook()` from `sfnb_setup.py`, each notebook has its
own `_config.yaml` that combines session context, EAI settings, and
language/package configuration in a single file:

```yaml
# All sections are optional -- session defaults are used when omitted

# Session context (overrides Snowpark session defaults)
context:
  warehouse: "MY_WH"
  database: "MY_DB"
  schema: "MY_SCHEMA"

# EAI settings
eai:
  managed: "MY_EAI"                        # EAI to ALTER (optional)
  supplementary_name: "MULTILANG_NOTEBOOK_EAI"  # name for auto-created EAI

# Language runtime and packages
languages:
  r:
    enabled: true
    conda_packages:
      - r-base
      - r-reticulate
    tarballs:
      snowflakeR: "https://github.com/Snowflake-Labs/snowflakeR/releases/download/v0.1.0/snowflakeR_0.1.0.tar.gz"
      RSnowflake: "https://github.com/Snowflake-Labs/RSnowflake/releases/download/v0.2.0/RSnowflake_0.2.0.tar.gz"
```

| Section | Purpose | Default |
|---------|---------|---------|
| `context` | Override session database/schema/warehouse | Session's current values |
| `eai.managed` | Name of EAI to `ALTER` when adding domains | Auto-discovered |
| `eai.supplementary_name` | Name for auto-created supplementary EAI | `MULTILANG_NOTEBOOK_EAI` |
| `languages` | Language runtimes and packages to install | R enabled |
| `languages.r.tarballs` | Map of package name to URL, local path, or omit for auto-search | `pak` from GitHub |

**Zero-config usage:** If no config file is provided, `setup_notebook()`
uses session defaults for context and installs R with no extra packages:

```python
from sfnb_setup import setup_notebook
setup_notebook(packages=["snowflakeR"])
```

## Top-Level Settings

| Key | Default | Description |
|---|---|---|
| `env_name` | `workspace_env` | Name of the micromamba environment |
| `micromamba_root` | `~/micromamba` | Where micromamba is installed |
| `force_reinstall` | `false` | Skip "already installed" checks |

## Languages

### R (`languages.r`)

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Install R |
| `r_version` | `""` (latest) | Pin R version (e.g. `"4.3.2"`) |
| `conda_packages` | tidyverse stack | List of conda-forge packages |
| `cran_packages` | `[]` | CRAN packages; `pkg==ver` for exact versions |
| `addons.adbc` | `false` | Install ADBC Snowflake driver |
| `addons.duckdb` | `false` | Install DuckDB with Snowflake extension |
| `tarballs` | `{}` | Map of R package name to URL or local path |

### Scala/Java (`languages.scala`)

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Install Scala/Java |
| `java_version` | `"17"` | OpenJDK version (11 or 17) |
| `scala_version` | `"2.12"` | Scala major version |
| `snowpark_version` | `"1.18.0"` | Snowpark JAR version |
| `ammonite_version` | `"3.0.8"` | Ammonite REPL version |
| `jvm_heap` | `"auto"` | JVM heap size ("auto" or e.g. "2g") |
| `jvm_options` | see default.yaml | Additional JVM flags |
| `extra_dependencies` | SLF4J NOP | Extra Maven coordinates |
| `spark_connect.enabled` | `false` | Set up Snowpark Connect |
| `spark_connect.pyspark_version` | `"3.5.6"` | PySpark version |
| `spark_connect.server_port` | `15002` | gRPC server port |

### Julia (`languages.julia`)

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Install Julia |
| `julia_version` | `""` (latest) | Pin Julia version |
| `julia_packages` | DataFrames stack | Julia packages to install |
| `depot_path` | `"auto"` | Julia depot location |
| `snowflake_odbc.enabled` | `false` | Install ODBC driver |
| `snowflake_odbc.driver_version` | `"3.15.0"` | ODBC driver version |
| `sysimage.enabled` | `false` | Build PackageCompiler sysimage |
| `sysimage.packages` | DataFrames stack | Packages to compile |
| `juliacall.threads` | `"auto"` | Julia thread count |
| `juliacall.optimize` | `2` | Julia optimization level |

## Custom Mirrors (`mirrors`)

Route package downloads through an internal artifact repository (JFrog
Artifactory, Sonatype Nexus, etc.). See [custom_mirrors.md](custom_mirrors.md)
for full setup instructions.

| Key | Default | Description |
|---|---|---|
| `conda_channel` | `conda-forge` | Conda channel URL (Artifactory/Nexus conda remote) |
| `pypi_index` | `pypi.org` | PyPI index URL (`--index-url` for pip) |
| `cran_mirror` | `cloud.r-project.org` | CRAN mirror URL for `install.packages()` |
| `micromamba_url` | `micro.mamba.pm` | Direct URL for micromamba binary download |
| `ssl_cert_path` | system CA bundle | Path to CA cert bundle for TLS inspection proxies |

When mirrors are configured, the EAI domain list is automatically
reduced to just the mirror host(s).

## Model Registry (`registry`)

Control the conda channel policy for Model Registry inference containers.
When set, `setup_notebook()` exports environment variables that snowflakeR's
`sfr_model_registry()` and `sfr_log_model()` pick up automatically.

| Key | Default | Description |
|---|---|---|
| `conda_channel` | `""` (Snowflake Anaconda Channel) | Conda channel name to prefix on all `conda_dependencies` passed to `log_model()`. Set to `"conda-forge"` to avoid Anaconda Inc. commercial channels. |
| `conda_channel_strict` | `false` | When `true`, users cannot override the channel at call-time. Any `sfr_log_model()` call that tries to use a different channel will error. |

**Example -- force conda-forge for all models:**

```yaml
registry:
  conda_channel: conda-forge
  conda_channel_strict: true
```

This exports `SFR_CONDA_CHANNEL=conda-forge` and
`SFR_CONDA_CHANNEL_STRICT=true` into the notebook process. Every
`sfr_log_model()` call inherits the channel automatically, and strict
mode prevents individual users from bypassing the policy.

**Note:** This controls the *inference container* channel (MODEL_BUILD in
SPCS). The `mirrors.conda_channel` setting controls where the *notebook
setup* downloads packages -- they are independent settings, though in
practice you'd typically set both to conda-forge.

## Network Rules (`network_rule`)

| Key | Default | Description |
|---|---|---|
| `apply_in_installer` | `true` | Try to apply EAI in Phase 0 |
| `account` | `""` | Snowflake account (auto-detected if empty) |
| `rule_name` | `multilang_notebook_egress` | Network rule name |
| `integration_name` | `multilang_notebook_eai` | EAI name |
| `grant_to_role` | `""` | Role to `GRANT USAGE ON INTEGRATION` to (allows that role to attach the EAI to notebooks) |
| `sql_export_path` | `./eai_setup.sql` | File path for SQL on failure |

## Logging (`logging`)

| Key | Default | Description |
|---|---|---|
| `level` | `"INFO"` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `log_file` | `""` | Optional file to write logs to |
| `json_format` | `false` | Use JSON format for file logs |

## CLI Flag Overrides

See [cli.md](cli.md) for the full CLI reference (subcommands, examples, and
a comparison with `setup_notebook()`).

CLI flags override YAML values:

| Flag | Overrides |
|---|---|
| `--r` | `languages.r.enabled: true` |
| `--scala` | `languages.scala.enabled: true` |
| `--julia` | `languages.julia.enabled: true` |
| `--all` | All languages enabled |
| `--r-adbc` | `languages.r.addons.adbc: true` |
| `--r-duckdb` | `languages.r.addons.duckdb: true` |
| `--verbose` | `logging.level: DEBUG` |
| `--force` | `force_reinstall: true` |
| `--no-eai` | `network_rule.apply_in_installer: false` |
| `--account X` | `network_rule.account: X` |
