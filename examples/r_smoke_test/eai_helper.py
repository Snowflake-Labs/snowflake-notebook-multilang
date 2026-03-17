"""Self-contained EAI helper for Workspace Notebook bootstrap cells.

No external dependencies beyond stdlib + yaml + Snowpark (all available
in every Snowflake Notebook before any pip install).

Usage (in a notebook cell)::

    from eai_helper import ensure_eai
    ensure_eai(session, lang_config="r_smoke_test_config.yaml")
"""

from __future__ import annotations

import json
import os
from typing import Optional

import yaml

# ── Domain lists (mirrors sfnb_multilang.network_rules) ──────────────────

SHARED_DOMAINS = [
    "micro.mamba.pm",
    "api.anaconda.org",
    "binstar-cio-packages-prod.s3.amazonaws.com",
    "conda.anaconda.org",
    "repo.anaconda.com",
]

TOOLKIT_DOMAINS = [
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
]

R_DOMAINS = [
    "cloud.r-project.org",
    "bioconductor.org",
]

R_ADBC_DOMAINS = [
    "community.r-multiverse.org",
    "cdn.r-universe.dev",
    "proxy.golang.org",
    "storage.googleapis.com",
    "sum.golang.org",
]

R_DUCKDB_DOMAINS = [
    "community-extensions.duckdb.org",
    "extensions.duckdb.org",
]

SCALA_DOMAINS = [
    "repo1.maven.org",
    "release-assets.githubusercontent.com",
]

JULIA_ODBC_DOMAINS = [
    "sfc-repo.snowflakecomputing.com",
]

DEFAULT_EAI_NAME = "multilang_notebook_eai"
DEFAULT_RULE_NAME = "multilang_notebook_egress"


# ── Config-driven domain resolution ─────────────────────────────────────

def _domains_from_config(config_path: str) -> set[str]:
    """Read an sfnb-multilang YAML config and derive required domains."""
    domains = set(SHARED_DOMAINS + TOOLKIT_DOMAINS)

    if not os.path.isfile(config_path):
        return domains

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    langs = cfg.get("languages", {})

    r_cfg = langs.get("r", {})
    if isinstance(r_cfg, bool):
        r_cfg = {"enabled": r_cfg}
    if r_cfg.get("enabled"):
        domains.update(R_DOMAINS)
        addons = r_cfg.get("addons", {})
        if addons.get("adbc"):
            domains.update(R_ADBC_DOMAINS)
        if addons.get("duckdb"):
            domains.update(R_DUCKDB_DOMAINS)

    scala_cfg = langs.get("scala", {})
    if isinstance(scala_cfg, bool):
        scala_cfg = {"enabled": scala_cfg}
    if scala_cfg.get("enabled"):
        domains.update(SCALA_DOMAINS)

    julia_cfg = langs.get("julia", {})
    if isinstance(julia_cfg, bool):
        julia_cfg = {"enabled": julia_cfg}
    if julia_cfg.get("enabled"):
        odbc = julia_cfg.get("snowflake_odbc", {})
        if odbc.get("enabled"):
            domains.update(JULIA_ODBC_DOMAINS)

    return domains


# ── SQL introspection helpers ────────────────────────────────────────────

def _parse_host_list(raw: str) -> set[str]:
    """Parse a VALUE_LIST string into domain names."""
    if not raw:
        return set()
    domains: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        part = part.strip().strip("'\"()[] ")
        if ":" in part:
            part = part.rsplit(":", 1)[0]
        if "." in part and part:
            domains.add(part.lower())
    return domains


def _eai_exists(session, eai_name: str) -> bool:
    try:
        rows = session.sql(
            f"SHOW EXTERNAL ACCESS INTEGRATIONS LIKE '{eai_name}'"
        ).collect()
        return len(rows) > 0
    except Exception:
        return False


def _get_eai_rule_names(session, eai_name: str) -> list[str]:
    """Discover network rule names from an existing EAI."""
    try:
        rows = session.sql(
            f"DESCRIBE EXTERNAL ACCESS INTEGRATION {eai_name}"
        ).collect()
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                continue
            for key in ("name", "property", "PROPERTY"):
                prop = str(d.get(key, "")).upper()
                if "ALLOWED_NETWORK_RULES" in prop:
                    val = str(d.get(
                        "value", d.get("property_value",
                                       d.get("VALUE", d.get(
                                           "PROPERTY_VALUE", "")))
                    ))
                    return [
                        r.strip().strip("[]'\"")
                        for r in val.split(",")
                        if r.strip().strip("[]'\"")
                    ]
    except Exception:
        pass
    return []


def _get_rule_domains(session, rule_name: str) -> set[str]:
    """Get current domains from an existing network rule.

    Tries multiple column-name conventions across Snowflake versions,
    then falls back to scanning every string value for hostname patterns.
    """
    try:
        rows = session.sql(f"DESCRIBE NETWORK RULE {rule_name}").collect()
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                d = {str(i): row[i] for i in range(len(row))}

            # Normalise keys to upper for matching
            upper_d = {str(k).upper(): v for k, v in d.items()}

            # Check if this row's property name relates to the value list
            prop_name = ""
            for k in ("NAME", "PROPERTY", "PROPERTY_NAME"):
                if k in upper_d:
                    prop_name = str(upper_d[k]).upper()
                    break

            if not any(kw in prop_name for kw in ("VALUE_LIST", "HOST_PORT", "VALUE")):
                continue

            # Extract the value from whichever column holds it
            raw = ""
            for k in ("VALUE", "PROPERTY_VALUE", "PROPERTY_DEFAULT"):
                if k in upper_d and upper_d[k]:
                    candidate = str(upper_d[k])
                    if "." in candidate:
                        raw = candidate
                        break

            if raw:
                return _parse_host_list(raw)

        # Fallback: scan all values of all rows for comma-separated hostnames
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                d = {str(i): row[i] for i in range(len(row))}
            for v in d.values():
                s = str(v)
                if s.count(".") >= 2 and ("," in s or "'" in s):
                    parsed = _parse_host_list(s)
                    if len(parsed) >= 2:
                        return parsed
    except Exception:
        pass
    return set()


# ── Service settings introspection ───────────────────────────────────────

SETTINGS_PATH = os.path.join(
    os.getcwd(), ".snowflake", "settings.json"
)


def _get_attached_eais(
    settings_path: str = SETTINGS_PATH,
) -> list[str]:
    """Read EAIs attached to this notebook service.

    Workspace Notebooks expose the current service configuration in
    ``.snowflake/settings.json``.  This file is written by the control
    plane at service startup (read-only from the notebook's perspective
    -- local writes are overwritten on restart).

    Returns a list of EAI names (upper-cased for comparison), or an
    empty list if the file is missing or unreadable.
    """
    try:
        with open(settings_path) as f:
            data = json.load(f)
        svc = (
            data
            .get("notebookSettings", {})
            .get("serviceDefaults", {})
        )
        raw = svc.get("externalAccessIntegrations", [])
        return [name.upper() for name in raw if name]
    except Exception:
        return []


# ── Main entry point ────────────────────────────────────────────────────

def ensure_eai(
    session,
    lang_config: str = "r_smoke_test_config.yaml",
    notebook_config: str = "notebook_config.yaml",
    eai_name: Optional[str] = None,
    rule_name: Optional[str] = None,
) -> dict:
    """Ensure the EAI exists and has all domains the config requires.

    1. Reads *notebook_config* for explicit EAI/rule names and session context.
    2. Reads *lang_config* to derive required domains from enabled languages.
    3. If the EAI exists: introspects its rule, merges missing domains.
    4. If the EAI doesn't exist: creates rule + EAI + grants usage.
    5. On permission failure: prints complete SQL for an admin.

    Returns dict with keys: eai_name, rule_name, action, domains_added
    """
    # -- Resolve names from notebook_config --------------------------------
    nb_cfg: dict = {}
    nb_path = os.path.join(os.getcwd(), notebook_config)
    if os.path.isfile(nb_path):
        with open(nb_path) as f:
            nb_cfg = yaml.safe_load(f) or {}

    eai_section = nb_cfg.get("eai", {}) or {}
    resolved_eai = eai_name or eai_section.get("name", DEFAULT_EAI_NAME)
    resolved_rule = (
        rule_name
        or eai_section.get("network_rule", DEFAULT_RULE_NAME)
    )

    # -- Set session context -----------------------------------------------
    ctx = nb_cfg.get("context", {})
    for key, cmd in [
        ("warehouse", "USE WAREHOUSE"),
        ("database", "USE DATABASE"),
        ("schema", "USE SCHEMA"),
    ]:
        val = ctx.get(key, "")
        if val and not val.startswith("<"):
            try:
                session.sql(f"{cmd} {val}").collect()
            except Exception:
                pass

    context_str = (
        f"{ctx.get('database', '?')}.{ctx.get('schema', '?')} "
        f"(warehouse: {ctx.get('warehouse', '?')})"
    )
    print(f"Session context: {context_str}")

    # -- Derive required domains from language config ----------------------
    lang_path = os.path.join(os.getcwd(), lang_config)
    required = _domains_from_config(lang_path)

    current_role = ""
    try:
        current_role = (session.get_current_role() or "").replace('"', '')
    except Exception:
        pass

    # -- Check if EAI is attached to the service ----------------------------
    attached = _get_attached_eais()
    is_attached = resolved_eai.upper() in attached

    result = {
        "eai_name": resolved_eai,
        "rule_name": resolved_rule,
        "action": "no_change",
        "attached": is_attached,
        "domains_added": [],
    }

    if is_attached:
        print(f"EAI '{resolved_eai}' is attached to this service.")
    elif attached:
        print(
            f"Service has EAI(s) {attached} but not "
            f"'{resolved_eai}'."
        )
    else:
        print("No EAIs attached to this service yet.")

    # -- EAI already exists: introspect and merge --------------------------
    if _eai_exists(session, resolved_eai):
        eai_rules = _get_eai_rule_names(session, resolved_eai)
        actual_rule = eai_rules[0] if eai_rules else resolved_rule

        current = _get_rule_domains(session, actual_rule)
        missing = required - current

        print(f"\nNetwork rule '{actual_rule}':")
        print(f"  Current domains : {len(current)}")
        print(f"  Required domains: {len(required)}")
        print(f"  Missing         : {len(missing)}")

        if not missing:
            print(f"\n  All {len(required)} required domains present.")
            _print_final_state(actual_rule, current)
            if is_attached:
                print("\nReady -- run Section 1.")
            else:
                _print_attach_instructions(resolved_eai)
            return result

        merged = sorted(current | required)
        host_list = ", ".join(f"'{h}'" for h in merged)
        alter_sql = (
            f"ALTER NETWORK RULE {actual_rule} "
            f"SET VALUE_LIST = ({host_list})"
        )

        try:
            session.sql(alter_sql).collect()
            added = sorted(missing)
            print(f"\n  Added {len(added)} domain(s):")
            for d in added:
                print(f"    + {d}")
            result["action"] = "updated"
            result["rule_name"] = actual_rule
            result["domains_added"] = added
            _print_final_state(actual_rule, merged)
            if is_attached:
                print("\nChanges take effect immediately.")
                print("Ready -- run Section 1.")
            else:
                _print_attach_instructions(resolved_eai)
            return result
        except Exception as exc:
            print(f"\nALTER failed (insufficient privileges?): {exc}")

    # -- EAI does not exist or ALTER failed: try CREATE --------------------
    all_domains = sorted(required)
    host_list = ", ".join(f"'{h}'" for h in all_domains)

    create_rule = (
        f"CREATE OR REPLACE NETWORK RULE {resolved_rule}\n"
        f"  MODE = EGRESS\n"
        f"  TYPE = HOST_PORT\n"
        f"  VALUE_LIST = ({host_list})"
    )
    create_eai = (
        f"CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {resolved_eai}\n"
        f"  ALLOWED_NETWORK_RULES = ({resolved_rule})\n"
        f"  ENABLED = TRUE"
    )
    grant = ""
    if current_role:
        grant = (
            f"GRANT USAGE ON INTEGRATION {resolved_eai} "
            f"TO ROLE {current_role}"
        )

    full_sql = f"{create_rule};\n\n{create_eai};\n"
    if grant:
        full_sql += f"\n{grant};\n"

    if not _eai_exists(session, resolved_eai):
        try:
            session.sql(create_rule).collect()
            print(f"Created network rule: {resolved_rule}")

            session.sql(create_eai).collect()
            print(f"Created EAI: {resolved_eai}")

            if grant:
                session.sql(grant).collect()
                print(f"Granted usage to role: {current_role}")

            result["action"] = "created"
            result["domains_added"] = all_domains
            _print_final_state(resolved_rule, all_domains)
            if not is_attached:
                _print_attach_instructions(resolved_eai)
            else:
                print("\nReady -- run Section 1.")
            return result
        except Exception as exc:
            print(f"CREATE failed (insufficient privileges?): {exc}")

    # -- Permission denied: print SQL for admin ----------------------------
    print(
        "\nCould not create or modify EAI "
        "(insufficient privileges)."
    )
    print("Share this SQL with your Snowflake admin:\n")
    print(full_sql)
    if not is_attached:
        _print_attach_instructions(resolved_eai)
    result["action"] = "print_sql"
    return result


def _print_final_state(rule_name, domains):
    """Print the full CREATE OR REPLACE statement reflecting current state."""
    if isinstance(domains, set):
        domains = sorted(domains)
    host_lines = "\n".join(f"    '{h}'" for h in domains)
    print(
        f"\n  Current state ({len(domains)} domains):\n"
        f"  ----------------------------------------\n"
        f"  CREATE OR REPLACE NETWORK RULE {rule_name}\n"
        f"    MODE = EGRESS\n"
        f"    TYPE = HOST_PORT\n"
        f"    VALUE_LIST = (\n{host_lines}\n    );\n"
        f"  ----------------------------------------"
    )


def _print_attach_instructions(eai_name: str) -> None:
    """Print Snowsight UI instructions for first-time EAI attachment."""
    print(
        f"\n  Attach '{eai_name}' to your notebook service:\n"
        f"    1. Click 'Connected' (top-left toolbar)\n"
        f"    2. Hover over service name > Edit\n"
        f"    3. Scroll to External Access\n"
        f"    4. Toggle ON '{eai_name}' > Save\n"
        f"    5. Service restarts automatically\n"
        f"    6. Run from Section 1\n"
    )
