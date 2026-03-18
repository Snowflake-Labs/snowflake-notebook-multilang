# Scala/Java Smoke Test -- Snowflake Workspace Notebook

Minimal validation that Scala and Java execute correctly in a Snowflake
Workspace Notebook via the sfnb-multilang toolkit.

## What This Tests

| # | Area | What's validated |
|---|------|-----------------|
| 1 | Scala execution | `%%scala` magic, values, string interpolation, collections |
| 2 | Java execution | `%%java` magic, variables, arrays, System.out |
| 3 | Snowpark Scala | Snowpark session query via `bootstrap_snowpark_scala()` |
| 4 | Variable transfer | Scala-to-Python data transfer via `-o` flag |

## Files to Upload

Upload **all files in this folder** to a Snowflake Workspace Notebook:

| File | Purpose |
|------|---------|
| `sfnb_setup.py` | All-in-one bootstrap: EAI, Scala/Java runtime, session context |
| `scala_smoke_test_config.yaml` | Language and (optional) session context / EAI config |
| `workspace_scala_smoke_test.ipynb` | The test notebook |

## Setup

### Single-cell bootstrap

The notebook's first code cell calls `setup_notebook()`, which handles
everything automatically:

```python
from sfnb_setup import setup_notebook
setup_notebook(config="scala_smoke_test_config.yaml")
```

This installs OpenJDK 17, Scala 2.12, Ammonite REPL, Snowpark JARs, and
JPype1 via micromamba + coursier.

After the setup cell, a second cell initializes the Scala environment:

```python
from scala_helpers import setup_scala_environment
setup_scala_environment()
```

### EAI (External Access Integration)

In addition to the standard domains (GitHub, PyPI, conda-forge), Scala
requires access to Maven Central for JAR resolution:

| Host | Purpose |
|------|---------|
| `repo1.maven.org` | Maven Central (coursier JAR resolution) |

These are included in the standard EAI domain set managed by `sfnb_setup.py`.

## Expected Runtime

| Step | First run | Cached |
|------|-----------|--------|
| EAI + context setup | ~5 sec | ~1 sec |
| Bootstrap Scala/Java environment | ~60 sec | ~2 sec |
| Run all tests | ~15 sec | ~15 sec |
| **Total** | **~1.5 min** | **~20 sec** |

## Interpreting Results

Each test section prints `[PASS]` on success. Common issues:

- **`Name or service not known`**: EAI not enabled -- see R smoke test README
  for EAI setup instructions (same process applies)
- **`repo1.maven.org` unreachable**: Add Maven Central to the EAI network rule
- **JVM crash**: Check that `java_version` in the config matches an available
  OpenJDK version (11 or 17)
