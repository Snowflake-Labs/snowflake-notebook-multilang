# Julia Smoke Test -- Snowflake Workspace Notebook

Minimal validation that Julia executes correctly in a Snowflake Workspace
Notebook via the sfnb-multilang toolkit.

## What This Tests

| # | Area | What's validated |
|---|------|-----------------|
| 1 | Julia execution | `%%julia` magic, variables, string interpolation, math |
| 2 | DataFrames | `using DataFrames`, DataFrame creation, column ops |
| 3 | Python-to-Julia transfer | `-i` flag, Python dict received in Julia |
| 4 | Julia-to-Python transfer | `-o` flag, Julia Dict received in Python |

## Files to Upload

Upload **all files in this folder** to a Snowflake Workspace Notebook:

| File | Purpose |
|------|---------|
| `sfnb_setup.py` | All-in-one bootstrap: EAI, Julia runtime, session context |
| `julia_smoke_test_config.yaml` | Language and (optional) session context / EAI config |
| `workspace_julia_smoke_test.ipynb` | The test notebook |

## Setup

### Single-cell bootstrap

The notebook's first code cell calls `setup_notebook()`, which handles
everything automatically:

```python
from sfnb_setup import setup_notebook
setup_notebook(config="julia_smoke_test_config.yaml")
```

This installs Julia via micromamba + conda-forge, then installs the
DataFrames, CSV, and Arrow packages via `Pkg.add()`.

After the setup cell, a second cell initializes the Julia environment:

```python
from julia_helpers import setup_julia_environment
setup_julia_environment()
```

### EAI (External Access Integration)

Julia packages are fetched from GitHub (with `JULIA_PKG_SERVER=""`), so the
standard EAI domains (github.com, objects.githubusercontent.com) are
sufficient. No additional Maven or CRAN hosts are needed.

## Expected Runtime

| Step | First run | Cached |
|------|-----------|--------|
| EAI + context setup | ~5 sec | ~1 sec |
| Bootstrap Julia environment | ~90 sec | ~2 sec |
| Julia package install | ~60 sec | ~5 sec |
| Run all tests | ~15 sec | ~15 sec |
| **Total** | **~3 min** | **~25 sec** |

Julia's first-run time is longer than R or Scala due to package
precompilation. Subsequent runs are fast once the conda environment
and Julia depot are cached.

## Interpreting Results

Each test section prints `[PASS]` on success. Common issues:

- **`Name or service not known`**: EAI not enabled -- see R smoke test README
  for EAI setup instructions (same process applies)
- **`Pkg.add` timeout**: Julia package server may be unreachable; the toolkit
  sets `JULIA_PKG_SERVER=""` to route through GitHub instead
- **Precompilation slow**: Normal on first run (~60 sec for DataFrames).
  Cached in the Julia depot on subsequent runs.
