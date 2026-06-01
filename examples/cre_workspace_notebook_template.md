# Workspace notebook template (CRE v2, no bootstrap cell)

Use when the notebook is attached to **`cre@sfnb_multilang_r`** (or your org CRE name) built from sfnb-multilang **v2+**.

## Snowsight setup

1. Workspace project (optional Git repo).
2. **Advanced settings** → Custom runtime environment → your CRE.
3. Compute pool + warehouse as usual.
4. Create notebook from cells below (or equivalent `.ipynb`).

## Cells

### Markdown: title

```markdown
# My analysis (CRE — no R bootstrap cell)

R and `%%R` are pre-baked in the container runtime. Optional Python cell sets session context only.
```

### Python (optional): context / EAI

```python
from sfnb_setup import setup_notebook

setup_notebook(
    config="/opt/sfnb/config/cre_multilang_r.yaml",  # baked in v2 image
    packages=["snowflakeR", "RSnowflake"],
)
```

Skip this cell if you only need `%%R` and set context via SQL.

### SQL (optional): context

```sql
USE ROLE my_notebook_role;
USE WAREHOUSE my_wh;
USE DATABASE my_db;
USE SCHEMA my_schema;
```

### R: connect

```r
%%R
library(snowflakeR)
conn <- sfr_connect()
conn
```

### R: analysis

```r
%%R
# your modelling code
```

## Verify CRE (first session)

```r
%%R
cat(R.version.string, "\n")
packageVersion("snowflakeR")
```

```python
import os
print("CRE_VERSION:", open("/opt/sfnb/CRE_VERSION").read().strip()
      if os.path.exists("/opt/sfnb/CRE_VERSION") else "not sfnb CRE")
```

## If `%%R` is unknown

- Confirm CRE is selected in advanced settings.
- Restart kernel / session.
- Org may be on CRE v1 — add one bootstrap cell with `enable_r_cells()` or upgrade image to v2.

## Bootstrap fallback

If CRE is not available, use [r_smoke_test](../examples/r_smoke_test/) or [quickstart.md](../docs/quickstart.md) bootstrap path.
