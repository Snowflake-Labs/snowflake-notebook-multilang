# Organisation operating model: CRE for R in Workspace

How **platform, ML ops, or IT** onboard a shared Custom Runtime Image for Workspace analysts — and when individuals still use **bootstrap** during a pilot.

This matches what many enterprises do in practice: a managed setup phase (image build, security review, `CREATE CUSTOM RUNTIME ENVIRONMENT`, role grants), then analysts consume `cre@<org_name>` without running Docker or install cells.

## Roles

| Role | Responsibility |
|------|----------------|
| **Platform / ML ops** | Own `cre_profile.yaml`, CI build, push, `CREATE OR REPLACE CRE`, grants |
| **Security** | Scan image in CI; approve digest; network rules for **build-time** EAI only if needed |
| **Analyst** | Attach `cre@<name>` in Workspace; use `%%R` + snowflakeR; optional `setup_notebook()` for context |
| **New hire / sandbox** | Bootstrap until org CRE exists |

## Lifecycle

```text
1. Fork cre_profile.example.yaml → configs/<org>_cre.yaml (not committed if policy requires)
2. ./docker/create_cre.sh configs/<org>_cre.yaml
3. PUSH=1 → image repository in YOUR account
4. CREATE OR REPLACE CUSTOM RUNTIME ENVIRONMENT <name> … BASE_IMAGE_TYPE = CPU|GPU
5. GRANT USAGE TO ROLE <notebook_roles>
6. Snowsight default or runbook: "Workspace R notebooks use cre@<name>"
7. On package upgrade: bump tag → rebuild → CREATE OR REPLACE → announce digest change
```

## Versioning policy

- Tag images with **sfnb version + snowbooks runtime** (e.g. `v1`, `v1.5.0-20260501`).
- Pin tarballs in profile YAML (`tarballs.snowflakeR`, `tarballs.RSnowflake`).
- Do not rely on `:latest` in production CRE registrations.
- Keep **CPU** and **GPU** images as separate CRE names.

## When analysts still run bootstrap

| Situation | Action |
|-----------|--------|
| Org CRE not ready | `setup_notebook()` + tarballs in YAML |
| One-off CRAN experiment | Bootstrap or rebuild CRE with `extras.cran` |
| Session context / EAI only on CRE | `setup_notebook(config="/opt/sfnb/config/cre_multilang_r.yaml")` — fast skip path |
| Wrong CRE attached | Fix advanced settings; restart session |

## CI/CD sketch

```yaml
# Pseudocode — adapt to your pipeline
- docker build --platform linux/amd64 -f docker/Dockerfile.multilang-r ...
- snow custom-image validate $IMAGE_TAG
- docker push $REGISTRY/$REPO/sfnb-multilang-r:$TAG
- snow sql -q "CREATE OR REPLACE CUSTOM RUNTIME ENVIRONMENT ..."
```

Store **digest** from `DESCRIBE CUSTOM RUNTIME ENVIRONMENT` in change tickets.

## Why organisations adopt CRE

| Challenge on default runtime | What CRE changes |
|-----------------------------|------------------|
| **~60 seconds** (or more) on each compute restart to run `setup_notebook()` | R, `%%R`, and approved packages are already in the image |
| Different package versions per notebook | One **versioned image** and digest for the whole team |
| Runtime downloads from CRAN/conda (EAI, audit) | Packages fixed at **build time**; security reviews the image once |
| Analysts wait on install cells before modelling | Reference **v1** image: open notebook and use **`%%R`** directly (empty git repo is fine) |

**Pilot vs production:** During evaluation, analysts can use **bootstrap** (one Python cell, no Docker). After your platform team registers `cre@<org_name>`, production notebooks should attach that CRE in Workspace advanced settings.

**Not a generic Docker image:** CRE must extend Snowflake’s official **snowbooks** runtime. This repository supplies the Dockerfile and `create_cre.sh` workflow; your team builds and hosts the image in **your** Snowflake image repository.

## Related docs

- [custom_runtime_images.md](custom_runtime_images.md)
- [cre_path_matrix.md](cre_path_matrix.md)
- [examples/cre_workspace_notebook_template.md](../examples/cre_workspace_notebook_template.md)
