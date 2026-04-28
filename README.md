# snowflake-notebook-multilang

> **Community Project -- Not Officially Supported**
> This is a community-developed project from
> [Snowflake Labs](https://github.com/Snowflake-Labs), not an official
> Snowflake offering. It is provided **as-is** without warranty or official
> Snowflake support. Use it for prototyping and experimentation; production
> use is at your own risk. Feedback, bug reports, and contributions are
> welcome via [GitHub Issues](https://github.com/Snowflake-Labs/snowflake-notebook-multilang/issues).

Multi-language support (R, Scala/Java, Julia) for Snowflake Workspace Notebooks.

This toolkit installs and configures language runtimes, packages, and
notebook magic commands so you can use R, Scala, Java, and Julia alongside
Python in Snowflake Workspace Notebooks.

## Quick Start

The fastest way to get R running in a Workspace Notebook is the all-in-one
`setup_notebook()` function from `sfnb_setup.py`:

```python
# Single cell -- handles EAI, R runtime, packages, and session context
from sfnb_setup import setup_notebook
setup_notebook(config="my_config.yaml", packages=["snowflakeR"])
```

`sfnb_setup.py` is a standalone bootstrap file (no pip install required)
that you upload alongside your notebook. It pip-installs `sfnb-multilang`
on first run and orchestrates the full setup.

**Alternative** -- use the `sfnb-multilang` API directly:

```python
# Cell 1: Install the toolkit
!pip install sfnb-multilang

# Cell 2: Install R and Scala
from sfnb_multilang import install
install(languages=["r", "scala"])
```

Three-language interop -- R, Python, and Scala each do what they're best at:

```python
# Cell 3: R — statistical summarisation, export to Python
%%R -o car_stats
library(dplyr)
car_stats <- mtcars %>%
  group_by(cyl) %>%
  summarise(mean_mpg = mean(mpg), mean_hp = mean(hp))
```

```python
# Cell 4: Python — feature engineering, load into Snowpark
car_stats["efficiency"] = (car_stats["mean_mpg"] / car_stats["mean_hp"]).round(3)
car_df = session.create_dataframe(car_stats)
```

```python
# Cell 5: Scala — Snowpark DataFrame operations
%%scala -i car_df
import com.snowflake.snowpark.functions.col
car_df.sort(col("efficiency").desc).show()
```

## Features

- **Single command** installs any combination of R, Scala/Java, and Julia
- **Fast** -- R base in ~45s, Scala/Java in ~30s, R + ADBC + DuckDB in ~2.5 min, Scala + Spark Connect in ~40s, cached re-runs in ~2s
- **Shared infrastructure** -- micromamba and JDK are installed once, not per-language
- **Automatic EAI management** -- multi-tier discovery, domain validation, and ALTER/CREATE with annotated SQL output
- **Configuration-driven** -- per-notebook YAML config with optional context, EAI, and tarball settings
- **Extensible** -- add new languages by implementing a Python plugin class
- **Structured logging** -- configurable text or JSON log output

## Installation

```bash
pip install sfnb-multilang
```

## Usage

### setup_notebook() (Recommended)

Upload `sfnb_setup.py` alongside your notebook (no pip install needed for
the bootstrap). Create a `_config.yaml` with your settings:

```yaml
# All sections are optional -- session defaults are used when omitted
context:
  warehouse: "MY_WH"
  database: "MY_DB"
  schema: "MY_SCHEMA"

eai:
  managed: "MY_EAI"

languages:
  r:
    enabled: true
    tarballs:
      snowflakeR: "https://github.com/Snowflake-Labs/snowflakeR/releases/download/v0.1.0/snowflakeR_0.1.0.tar.gz"
```

```python
from sfnb_setup import setup_notebook
setup_notebook(config="my_config.yaml", packages=["snowflakeR"])
```

### From a Notebook Cell (Programmatic API)

```python
from sfnb_multilang import install, generate_eai_sql, apply_eai

# Install with a config file
install(config="config.yaml")

# Install with keyword arguments
install(languages=["r", "scala"], r_adbc=True)

# Generate EAI SQL (prints to output)
sql = generate_eai_sql(languages=["r", "scala"])
print(sql)

# Apply EAI directly (requires CREATE INTEGRATION privilege)
apply_eai(session, languages=["r", "scala"])
```

### From the Command Line (CLI)

```bash
# Install from config
sfnb-setup install --config config.yaml

# Install specific languages
sfnb-setup install --r --scala --verbose

# Generate EAI SQL
sfnb-setup generate-eai --r --scala --account myaccount

# Validate existing installation
sfnb-setup validate --config config.yaml

# Migrate per-language YAML configs to unified format
sfnb-setup migrate-config \
  --r-config r_packages.yaml \
  --scala-config scala_packages.yaml \
  --output config.yaml
```

## Preset Configurations

Ready-made configs in the `configs/` directory:

| File | Languages |
|---|---|
| `default.yaml` | R + Scala/Java + Julia |
| `r_only.yaml` | R |
| `scala_only.yaml` | Scala/Java |
| `julia_only.yaml` | Julia |
| `r_scala.yaml` | R + Scala/Java |

## Network Rules (EAI)

Snowflake Workspace Notebooks block outbound traffic by default. The
toolkit can automatically generate (and optionally apply) the External
Access Integration SQL needed for package downloads.

`setup_notebook()` includes built-in multi-tier EAI discovery and
management -- it discovers attached EAIs, validates domains, and
creates or modifies network rules as needed.

See [docs/network_rules.md](docs/network_rules.md) for details.

## Examples

The `examples/` directory contains ready-to-run Workspace Notebooks:

| Example | What it tests |
|---|---|
| [R Smoke Test](examples/r_smoke_test/) | End-to-end validation of R, snowflakeR (Model Registry, Feature Store), and RSnowflake (DBI) |

Each example includes a README with setup instructions, EAI configuration,
and all files needed to upload to a Workspace Notebook.

## Documentation

- [Quick Start Guide](docs/quickstart.md)
- [Configuration Reference](docs/configuration.md)
- [Network Rules](docs/network_rules.md)
- [Custom Mirrors (Artifactory / Nexus)](docs/custom_mirrors.md)
- [Artifact repos & conda compliance (Workspace + snowflakeR)](docs/artifact_repository_support_summary.md)
- [Adding a New Language](docs/adding_a_language.md)

## Requirements

- Python >= 3.9 (included in Snowflake Workspace Notebooks)
- `pyyaml >= 6.0`
- Network access configured via EAI

## License

Apache 2.0. See [LICENSE](LICENSE).
