# CRE vs bootstrap path matrix

| Path | R in image? | Time to first `%%R` | Governance | When to use |
|------|-------------|---------------------|------------|-------------|
| **CRE v1 (after org IT onboarding)** | Yes — extend snowbooks | Seconds (after container up) | Image CI scan, pinned digest | Production notebooks post go-live |
| **Cold bootstrap** | Installed at runtime | ~60s typical; longer with extras | Runtime downloads (EAI) | Pilot before CRE; package experiments |
| **GPU CRE + GPU pool** | Yes — GPU snowbooks base | Same as CRE v1 | Separate GPU image tag (`v1-gpu`) | R `torch`, GPU Python ML |
| **Local IDE (RStudio / VS Code)** | On laptop | N/A | `connections.toml` / PAT | Not Workspace |

## Decision tree

```text
Need R in Workspace Notebook?
├─ Org can build/register CRE? → YES → cre@sfnb_multilang_r (CPU tag v1) or v1-gpu (GPU pool)
│                                 NO  → setup_notebook() bootstrap cell + EAI
└─ Git repo on /filesystem with renv?
    → Prefer CRE v1 or enable_r_cells(); avoid raw %load_ext rpy2
```

## Notes

- **renv** on git-mounted `/filesystem` — prefer reference CRE **v1** or `enable_r_cells()`; avoid raw `%load_ext rpy2`.
- Registry images still tagged `:v2` from early development are the same recipe as **v1** — rebuild with current `build_cre.sh` defaults when convenient.
