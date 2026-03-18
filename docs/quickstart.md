# Quick Start Guide

## Prerequisites

1. A Snowflake Workspace Notebook
2. An External Access Integration (EAI) configured for outbound network
   access. `setup_notebook()` can create/manage this automatically.

## Recommended: setup_notebook() (single cell)

Upload `sfnb_setup.py` alongside your notebook and create a `_config.yaml`:

```python
from sfnb_setup import setup_notebook
setup_notebook(config="my_config.yaml", packages=["snowflakeR"])
```

This handles EAI validation, R runtime installation, R package installation,
and session context in a single call. See the
[R Smoke Test](../examples/r_smoke_test/) for a complete working example.

## Alternative: Step-by-step setup

### Step 1: Install the Toolkit

In a notebook cell:

```python
!pip install sfnb-multilang
```

### Step 2: Configure Network Access

`setup_notebook()` handles EAI automatically. If you prefer manual control,
the toolkit can attempt to create the EAI. If your role has
`CREATE INTEGRATION` privilege:

```python
from sfnb_multilang import apply_eai
apply_eai(session, languages=["r", "scala"])
```

If you lack privileges, the installer will print the SQL and save it to
`eai_setup.sql` -- share this with your Snowflake administrator.

After the EAI is created, enable it in Snowsight:
**Connected > Edit > External Access > toggle on the EAI > Save**

Once created and attached, the same EAI can be reused across multiple
Workspace Notebooks.

### Step 3: Install Languages

```python
from sfnb_multilang import install

# Install R and Scala
install(languages=["r", "scala"])

# Or install from a config file
install(config="config.yaml")
```

**Typical install times (fresh container):**

| Configuration | Time |
|---|---|
| R base + tidyverse | ~45s |
| R + ADBC Snowflake driver | ~2 min (Go compilation) |
| R + ADBC + DuckDB Snowflake extension | ~2.5 min |
| Scala/Java + Snowpark | ~30s |
| Scala/Java + Snowpark + Spark Connect | ~40s |
| Subsequent runs (cached) | ~2s |

ADBC is the slowest step because it compiles the Go-based Snowflake
driver from source. DuckDB adds ~10s for extension downloads.
Spark Connect adds ~10s for PySpark and client JAR resolution.

### Step 4: Use the Languages

After `install()` (or `setup_notebook()`), the `%%R`, `%%scala`, and
`%%julia` magics are registered automatically.

#### R

```r
%%R
library(dplyr)
mtcars %>% group_by(cyl) %>% summarise(mean_mpg = mean(mpg))
```

#### Scala

```python
from scala_helpers import setup_scala_environment
setup_scala_environment()
```

```scala
%%scala
val x = 42
println(s"The answer is $x")
```

#### Julia

```python
from julia_helpers import setup_julia_environment
setup_julia_environment()
```

```julia
%%julia
using DataFrames
df = DataFrame(x=1:10, y=rand(10))
describe(df)
```

## Multi-Language Cell Ordering (Scala + Julia)

When using **both Scala and Julia** in the same notebook, the setup
cells must follow a specific order. The JVM (started by
`setup_scala_environment`) and Julia (started by
`setup_julia_environment`) both install native signal handlers. If
Julia is initialised first and a Snowpark Scala session is created
afterwards, the conflicting handlers cause a kernel crash (SIGSEGV).

**Required order:**

```python
# 1. R (safe at any position -- setup_notebook handles this)
from sfnb_setup import setup_notebook
setup_notebook(config="my_config.yaml")

# 2. Scala -- start JVM
from scala_helpers import setup_scala_environment
setup_scala_environment()

# 3. Snowpark Scala session -- MUST happen before Julia
from snowflake.snowpark.context import get_active_session
from scala_helpers import bootstrap_snowpark_scala
session = get_active_session()
bootstrap_snowpark_scala(session)

# 4. Julia -- last, after all JVM network operations
from julia_helpers import setup_julia_environment
setup_julia_environment()
```

The toolkit will emit a warning if it detects the wrong order, and
`bootstrap_snowpark_scala` will refuse to run if Julia is already
active (to prevent an unrecoverable crash).

Julia install times are dominated by package precompilation (~7 min
for DataFrames, CSV, Arrow, PythonCall on a fresh container). Cached
re-runs complete in ~2s.

## Using a YAML Config

Create a `config.yaml` (or use one of the presets in `configs/`):

```yaml
languages:
  r:
    enabled: true
    conda_packages:
      - r-tidyverse
      - r-dbplyr
    addons:
      adbc: true
  scala:
    enabled: true
    spark_connect:
      enabled: true
```

Then:

```python
from sfnb_multilang import install
install(config="config.yaml")
```

## CLI Usage

From a `!` shell cell or terminal:

```bash
sfnb-setup install --r --scala --verbose
sfnb-setup generate-eai --r --scala --account myaccount
sfnb-setup validate --config config.yaml
```
