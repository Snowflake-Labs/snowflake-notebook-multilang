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
    "release-assets.githubusercontent.com",
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


# ── EAI discovery ────────────────────────────────────────────────────────
#
# .snowflake/settings.json is a private implementation detail -- it is
# created lazily and NOT guaranteed to exist.  We use it as a best-effort
# hint only; the primary discovery path is SQL-based.
#


def _hint_eais_from_settings() -> list[str]:
    """Best-effort: read EAI names from .snowflake/settings.json.

    This file is NOT a stable contract (per Snowflake engineering).  It is
    created lazily and may not exist in fresh Workspaces.  Returns an
    empty list if not found or unreadable.
    """
    candidates = [
        os.path.join(os.getcwd(), ".snowflake", "settings.json"),
    ]
    d = os.getcwd()
    for _ in range(5):
        p = os.path.join(d, ".snowflake", "settings.json")
        if p not in candidates:
            candidates.append(p)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    for root in ("/home/jupyter", "/home"):
        p = os.path.join(root, ".snowflake", "settings.json")
        if p not in candidates:
            candidates.append(p)
    try:
        for entry in os.listdir("/filesystem"):
            p = os.path.join("/filesystem", entry, ".snowflake", "settings.json")
            if p not in candidates:
                candidates.append(p)
    except OSError:
        pass

    for path in candidates:
        try:
            with open(path) as f:
                data = json.load(f)
            svc = (
                data
                .get("notebookSettings", {})
                .get("serviceDefaults", {})
            )
            raw = svc.get("externalAccessIntegrations", [])
            names = [name.upper() for name in raw if name]
            if names:
                return names
        except Exception:
            continue
    return []


def _discover_eais_via_sql(session) -> list[str]:
    """Discover EAIs visible to the current role via SHOW command.

    Returns a list of enabled EAI names (upper-cased) the role can see.
    """
    try:
        rows = session.sql(
            "SHOW EXTERNAL ACCESS INTEGRATIONS"
        ).collect()
        names = []
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                continue
            upper_d = {str(k).upper(): v for k, v in d.items()}
            enabled = str(upper_d.get("ENABLED", "")).lower()
            if enabled not in ("true", "1"):
                continue
            name = str(upper_d.get("NAME", "")).upper()
            if name:
                names.append(name)
        return names
    except Exception:
        return []


def _is_open_eai(session, eai_name: str) -> bool:
    """Check whether an EAI is effectively open (allows all egress).

    Detects wildcard patterns: 0.0.0.0/0 (IPV4) or 0.0.0.0:port (HOST_PORT).
    """
    rules = _get_eai_rule_names(session, eai_name)
    if not rules:
        return False
    for rule_name in rules:
        try:
            rows = session.sql(
                f"DESCRIBE NETWORK RULE {rule_name}"
            ).collect()
            for row in rows:
                try:
                    d = row.as_dict()
                except Exception:
                    d = {str(i): row[i] for i in range(len(row))}
                for v in d.values():
                    s = str(v)
                    if "0.0.0.0/0" in s or "0.0.0.0:" in s:
                        return True
        except Exception:
            continue
    return False


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
    # Use session's existing context as defaults; notebook_config overrides.
    ctx = nb_cfg.get("context", {}) or {}
    _strip = lambda s: (s or "").replace('"', '')
    session_defaults = {
        "warehouse": _strip(session.get_current_warehouse()),
        "database": _strip(session.get_current_database()),
        "schema": _strip(session.get_current_schema()),
    }
    for key, cmd in [
        ("warehouse", "USE WAREHOUSE"),
        ("database", "USE DATABASE"),
        ("schema", "USE SCHEMA"),
    ]:
        cfg_val = ctx.get(key, "")
        if cfg_val and not cfg_val.startswith("<"):
            try:
                session.sql(f"{cmd} {cfg_val}").collect()
            except Exception:
                pass
        elif not session_defaults.get(key):
            pass  # no override and no session default -- skip

    # Re-read the effective context after any USE statements
    effective = {
        "warehouse": _strip(session.get_current_warehouse()) or "?",
        "database": _strip(session.get_current_database()) or "?",
        "schema": _strip(session.get_current_schema()) or "?",
    }
    context_str = (
        f"{effective['database']}.{effective['schema']} "
        f"(warehouse: {effective['warehouse']})"
    )
    print(f"Session context: {context_str}")
    if not ctx:
        print("  (using session defaults -- no notebook_config overrides)")

    # -- Derive required domains from language config ----------------------
    lang_path = os.path.join(os.getcwd(), lang_config)
    required = _domains_from_config(lang_path)

    current_role = ""
    try:
        current_role = (session.get_current_role() or "").replace('"', '')
    except Exception:
        pass

    # -- Multi-tier EAI discovery ------------------------------------------
    # 1. Explicit name from notebook_config.yaml / function parameter
    # 2. Best-effort hint from .snowflake/settings.json (not guaranteed)
    # 3. SHOW EXTERNAL ACCESS INTEGRATIONS (SQL -- role-dependent)
    # 4. Convention name fallback
    #
    # .snowflake/settings.json is a private implementation detail and may
    # not exist.  We never rely on it as the sole discovery mechanism.

    user_specified = bool(eai_name or eai_section.get("name"))

    # Tier 2: best-effort settings.json hint
    settings_eais = _hint_eais_from_settings()

    # Tier 3: SQL discovery
    sql_eais = _discover_eais_via_sql(session)

    # Build a unified candidate list (preserving priority order)
    eai_candidates: list[str] = []
    if user_specified:
        eai_candidates.append(resolved_eai.upper())
    for name in settings_eais:
        if name not in eai_candidates:
            eai_candidates.append(name)
    for name in sql_eais:
        if name not in eai_candidates:
            eai_candidates.append(name)
    if resolved_eai.upper() not in eai_candidates:
        eai_candidates.append(resolved_eai.upper())

    # Pick the first candidate that actually exists
    chosen_eai = resolved_eai
    discovery_source = "convention name"
    for candidate in eai_candidates:
        if _eai_exists(session, candidate):
            chosen_eai = candidate
            if candidate == resolved_eai.upper() and user_specified:
                discovery_source = "notebook_config"
            elif candidate in settings_eais:
                discovery_source = "settings.json (hint)"
            elif candidate in sql_eais:
                discovery_source = "SHOW INTEGRATIONS"
            else:
                discovery_source = "convention name"
            break

    resolved_eai = chosen_eai

    # Discover the rule name from the chosen EAI
    eai_rules = _get_eai_rule_names(session, resolved_eai)
    if eai_rules:
        resolved_rule = eai_rules[0]

    result = {
        "eai_name": resolved_eai,
        "rule_name": resolved_rule,
        "action": "no_change",
        "discovery": discovery_source,
        "domains_added": [],
    }

    eai_found = _eai_exists(session, resolved_eai)

    if eai_found:
        print(f"EAI '{resolved_eai}' found (via {discovery_source}).")
    else:
        print(f"No existing EAI found (tried: {', '.join(eai_candidates)}).")

    # -- Open-EAI detection ------------------------------------------------
    if eai_found and _is_open_eai(session, resolved_eai):
        print(
            f"  EAI '{resolved_eai}' allows all egress (open EAI) -- "
            f"no domain changes needed."
        )
        result["action"] = "open_eai"
        print("\nReady -- run Section 1.")
        return result

    # -- EAI exists: introspect and merge ----------------------------------
    if eai_found:
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
            print("\nReady -- run Section 1.")
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
            print("\nChanges take effect immediately.")
            print("Ready -- run Section 1.")
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

    if not eai_found:
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
            _print_attach_instructions(resolved_eai)
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
