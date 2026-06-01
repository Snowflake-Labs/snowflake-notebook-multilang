# Path matrix: R in Snowflake Workspace

Choose the right entry path for your team and constraints.

| Path | Docker / CRE | Cold `%%R` | Governance | Best for |
|------|--------------|------------|------------|----------|
| **CRE v2 (after org IT onboarding)** | Yes — extend snowbooks | Seconds (after container up) | Image CI scan, pinned digest | Production notebooks post go-live |
| **Bootstrap `setup_notebook()`** | No | ~60s typical per compute restart (package-dependent) | Runtime EAI + YAML | PoC, first day, package experiments |
| **CRE v1 (legacy)** | Yes | R baked; bootstrap for `%%R` | Same as CRE | Migrate to v2 |
| **GPU CRE + GPU pool** | Yes — GPU snowbooks base | Same as CRE v2 | Separate GPU image tag | R `torch`, GPU Python ML |
| **Managed GPU runtime (no CRE)** | No | R install at session + GPU Python stack | Snowflake-managed base | Python-first GPU + some `%%R` |
| **Local RStudio / Posit / VS Code** | N/A | N/A | `connections.toml` / key-pair | Desktop IDE, not Workspace |
| **Custom SPCS / JupyterHub** | Full Dockerfile control | Pre-baked in service | You own image | RStudio Server, batch jobs, IRkernel |

## Decision flow

```text
Need R in Workspace Notebook?
├─ Org can build/register CRE? → YES → cre@sfnb_multilang_r v2 (CPU) or v2-gpu (GPU pool)
└─ NO → setup_notebook() bootstrap + tarballs in YAML
         └─ Later → migrate to CRE (same packages, baked at build)
```

## Compatibility notes

- **%%R** is used on all Workspace paths (not a separate R kernel in Snowsight today).
- **snowflakeR** session bridge expects the **Python kernel** + rpy2 (CRE and bootstrap).
- **renv** on git-mounted `/filesystem` — prefer CRE v2 or `enable_r_cells()`; avoid raw `%load_ext rpy2`.

See [custom_runtime_images.md](custom_runtime_images.md) and the [Hitchhiker's Guide to R in Snowflake](https://snowflake-labs.github.io/snowflakeR/).
