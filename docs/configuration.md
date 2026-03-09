# Configuration Reference

The toolkit is configured via a YAML file. Every section is optional;
omitted sections use sensible defaults.

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

## Network Rules (`network_rule`)

| Key | Default | Description |
|---|---|---|
| `apply_in_installer` | `true` | Try to apply EAI in Phase 0 |
| `account` | `""` | Snowflake account (auto-detected if empty) |
| `rule_name` | `multilang_notebook_egress` | Network rule name |
| `integration_name` | `multilang_notebook_eai` | EAI name |
| `grant_to_role` | `""` | Optional role to GRANT USAGE to |
| `sql_export_path` | `./eai_setup.sql` | File path for SQL on failure |

## Logging (`logging`)

| Key | Default | Description |
|---|---|---|
| `level` | `"INFO"` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `log_file` | `""` | Optional file to write logs to |
| `json_format` | `false` | Use JSON format for file logs |

## CLI Flag Overrides

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
