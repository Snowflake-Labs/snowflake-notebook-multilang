"""Bootstrap sfnb_multilang for Snowflake Workspace Notebooks.

Single file to set up a Workspace Notebook: session context, EAI
validation, language runtime installation, and R package installation.

No external dependencies beyond stdlib + yaml + Snowpark (all available
in every Snowflake Notebook before any pip install).

DERIVED COPY -- Do not edit directly.
  The canonical source is:
    snowflake-notebook-multilang/src/sfnb_multilang/helpers/sfnb_setup.py
  Edit there, then copy here.

Usage in notebooks:

    # Single-cell setup (recommended):
    from sfnb_setup import setup_notebook
    setup_notebook(config="snowflaker_config.yaml", packages=["snowflakeR"])

    # Minimal (zero config):
    from sfnb_setup import setup_notebook
    setup_notebook(packages=["snowflakeR"])

    # Power user (separate steps):
    from sfnb_setup import ensure_eai, install_r, install_r_packages
    ensure_eai(session, config="my_config.yaml")
    install_r(config="my_config.yaml")
    install_r_packages(config="my_config.yaml", packages=["snowflakeR"])

    # Test public-user experience from within the monorepo:
    import os; os.environ["SFNB_PUBLIC_MODE"] = "1"
    from sfnb_setup import setup_notebook
    setup_notebook(config="my_config.yaml")
"""
from __future__ import annotations

import datetime
import glob
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Optional


# ==========================================================================
# Section 1: Logging
# ==========================================================================

_LOG_LINES: list[str] = []


def _log(msg: str, *, quiet: bool = False):
    """Print and record a log line."""
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _LOG_LINES.append(line)
    if not quiet:
        print(msg)


def _flush_log():
    """Write accumulated log lines to .sfnb_setup.log."""
    candidates = [
        os.path.join(os.getcwd(), ".sfnb_setup.log"),
        os.path.expanduser("~/.sfnb_setup.log"),
        "/tmp/.sfnb_setup.log",
    ]
    content = "\n".join(_LOG_LINES) + "\n"
    for path in candidates:
        try:
            with open(path, "w") as f:
                f.write(content)
            _LOG_LINES.append(f"[log] Written to {path}")
            return
        except OSError:
            continue


def _find_log_path():
    """Return the path where the log was actually written, or None."""
    for path in [
        os.path.join(os.getcwd(), ".sfnb_setup.log"),
        os.path.expanduser("~/.sfnb_setup.log"),
        "/tmp/.sfnb_setup.log",
    ]:
        if os.path.exists(path):
            return path
    return None


# ==========================================================================
# Section 1b: Mirrors config helpers
# ==========================================================================

def _get_mirrors(cfg: dict) -> dict:
    """Extract mirrors section from parsed YAML config."""
    raw = cfg.get("mirrors", {}) or {}
    return {
        "conda_channel": str(raw.get("conda_channel", "")),
        "pypi_index": str(raw.get("pypi_index", "")),
        "pypi_extra_index": str(raw.get("pypi_extra_index", "")),
        "cran_mirror": str(raw.get("cran_mirror", "")),
        "micromamba_url": str(raw.get("micromamba_url", "")),
        "ssl_cert_path": str(raw.get("ssl_cert_path", "")),
        "auth_secret": str(raw.get("auth_secret", "")),
    }


def _normalize_secret_path(raw: str) -> str:
    """Accept ``db.schema.name`` or ``db/schema/name``; return lowercase slash form."""
    path = raw.replace(".", "/") if "/" not in raw else raw
    return path.lower()


def _read_mirror_credentials(auth_secret: str) -> tuple[str, str]:
    """Read username/password from a Snowflake PASSWORD secret.

    Tries the Snowpark Secrets API first, then falls back to the container
    mount path ``/secrets/<db>/<schema>/<name>/{username,password}``.
    Returns ``("", "")`` if the secret cannot be read.
    """
    if not auth_secret:
        return ("", "")

    secret_path = _normalize_secret_path(auth_secret)

    try:
        from snowflake.snowpark.secrets import get_username_password
        creds = get_username_password(secret_path)
        return (creds.username, creds.password)
    except Exception:
        pass

    mount = f"/secrets/{secret_path}"
    try:
        username = open(f"{mount}/username").read().strip()
        password = open(f"{mount}/password").read().strip()
        return (username, password)
    except (OSError, FileNotFoundError):
        pass

    _log(
        f"  WARNING: auth_secret '{auth_secret}' configured but credentials "
        f"could not be read. Proceeding without mirror authentication.",
        quiet=False,
    )
    return ("", "")


def _inject_auth_url(url: str, username: str, password: str) -> str:
    """Embed ``username:password@`` into a URL for basic-auth."""
    if not url or not username:
        return url
    from urllib.parse import urlparse, quote
    parsed = urlparse(url)
    if parsed.username:
        return url
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    host_port = parsed.hostname
    if parsed.port:
        host_port += f":{parsed.port}"
    result = f"{parsed.scheme}://{userinfo}@{host_port}{parsed.path}"
    if parsed.query:
        result += f"?{parsed.query}"
    return result


def _mask_url_credentials(url: str) -> str:
    """Replace ``user:pass@host`` with ``user:****@host`` for safe logging."""
    if not url:
        return url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.password:
        return url
    host_port = parsed.hostname
    if parsed.port:
        host_port += f":{parsed.port}"
    result = f"{parsed.scheme}://{parsed.username}:****@{host_port}{parsed.path}"
    if parsed.query:
        result += f"?{parsed.query}"
    return result


def _apply_mirror_auth(mirrors: dict) -> dict:
    """Resolve auth_secret and inject credentials into all mirror URLs.

    Returns a new mirrors dict with authenticated URLs. The original
    dict is not modified. If no auth_secret is configured or credentials
    cannot be read, the original URLs are returned unchanged.
    """
    auth_secret = mirrors.get("auth_secret", "")
    if not auth_secret:
        return mirrors

    username, password = _read_mirror_credentials(auth_secret)
    if not username:
        return mirrors

    authed = dict(mirrors)
    for key in ("conda_channel", "pypi_index", "pypi_extra_index", "cran_mirror", "micromamba_url"):
        if authed.get(key):
            authed[key] = _inject_auth_url(authed[key], username, password)
    return authed


def _apply_registry_env(cfg: dict, quiet: bool = False):
    """Export SFR_CONDA_CHANNEL env vars from the registry: YAML section.

    These env vars are read by snowflakeR's sfr_model_registry() and
    sfr_log_model() to enforce a conda channel policy for Model Registry
    inference containers (MODEL_BUILD).
    """
    raw = cfg.get("registry", {}) or {}
    channel = str(raw.get("conda_channel", ""))
    strict = raw.get("conda_channel_strict", False)

    if channel:
        os.environ["SFR_CONDA_CHANNEL"] = channel
        if strict:
            os.environ["SFR_CONDA_CHANNEL_STRICT"] = "true"
        _log(
            f"  Registry conda channel: {channel}"
            f"{' (strict)' if strict else ''}",
            quiet=quiet,
        )


def _pip_index_flags(mirrors: dict) -> list[str]:
    """Build pip --index-url / --extra-index-url / --cert flags from mirrors config."""
    flags: list[str] = []
    if mirrors.get("pypi_index"):
        flags += ["--index-url", mirrors["pypi_index"]]
    if mirrors.get("pypi_extra_index"):
        flags += ["--extra-index-url", mirrors["pypi_extra_index"]]
    cert = mirrors.get("ssl_cert_path", "")
    if cert and os.path.isfile(cert):
        flags += ["--cert", cert]
    return flags


# ==========================================================================
# Section 1c: Pre-warm heavy ML imports
# ==========================================================================

def _prewarm_ml_imports(quiet: bool = False, mirrors: dict | None = None):
    """Pre-import heavy snowflake-ml modules so first user cell is fast.

    The first ``from snowflake.ml.feature_store import FeatureStore`` triggers
    a cascading import of numpy, pandas, scikit-learn, ray, etc. that can take
    60-120s in SPCS containers.  Doing it here moves the cost into the setup
    phase where the user already expects a wait.

    Also installs ``ipywidgets`` to suppress the "Missing packages" INFO
    message that Ray emits on import (unusable in Workspace but noisy).
    """
    try:
        import importlib.util
        if importlib.util.find_spec("ipywidgets") is None:
            pip_flags = _pip_index_flags(mirrors or {})
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", "ipywidgets"]
                + pip_flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass

    t0 = time.monotonic()
    try:
        import snowflake.ml.feature_store  # noqa: F401
        elapsed = time.monotonic() - t0
        _log(f"  Pre-warmed snowflake.ml imports: {elapsed:.0f}s", quiet=quiet)
    except ImportError:
        pass
    except Exception as exc:
        _log(f"  Pre-warm snowflake.ml imports skipped: {exc}", quiet=quiet)


# ==========================================================================
# Section 2: YAML config reader
# ==========================================================================

def _read_config(config: str | None) -> dict:
    """Read a YAML config file. Returns {} if not found or None."""
    if not config:
        return {}
    path = config if os.path.isabs(config) else os.path.join(os.getcwd(), config)
    if not os.path.isfile(path):
        return {}
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ==========================================================================
# Section 3: Session context
# ==========================================================================

def _set_session_context(session, cfg: dict, quiet: bool = False) -> dict:
    """Set session database/schema/warehouse from config or session defaults."""
    ctx = cfg.get("context", {}) or {}
    _strip = lambda s: (s or "").replace('"', '')

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

    effective = {
        "warehouse": _strip(session.get_current_warehouse()) or "?",
        "database": _strip(session.get_current_database()) or "?",
        "schema": _strip(session.get_current_schema()) or "?",
    }
    context_str = (
        f"{effective['database']}.{effective['schema']} "
        f"(warehouse: {effective['warehouse']})"
    )
    _log(f"Session context: {context_str}", quiet=quiet)
    if not ctx:
        _log("  (using session defaults)", quiet=quiet)
    return effective


# ==========================================================================
# Section 4: EAI domain lists and resolution
# ==========================================================================

SHARED_DOMAINS = [
    "micro.mamba.pm", "api.anaconda.org",
    "binstar-cio-packages-prod.s3.amazonaws.com",
    "conda.anaconda.org", "repo.anaconda.com",
]
TOOLKIT_DOMAINS = [
    "pypi.org", "files.pythonhosted.org", "github.com",
    "api.github.com", "codeload.github.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
]
R_DOMAINS = ["cloud.r-project.org", "bioconductor.org"]
R_ADBC_DOMAINS = [
    "community.r-multiverse.org", "cdn.r-universe.dev",
    "proxy.golang.org", "storage.googleapis.com", "sum.golang.org",
]
R_DUCKDB_DOMAINS = ["community-extensions.duckdb.org", "extensions.duckdb.org"]
SCALA_DOMAINS = ["repo1.maven.org"]
JULIA_ODBC_DOMAINS = ["sfc-repo.snowflakecomputing.com"]

DEFAULT_SUPPLEMENTARY_NAME = "MULTILANG_NOTEBOOK_EAI"
DEFAULT_RULE_NAME = "MULTILANG_NOTEBOOK_EGRESS"


def _domains_from_config(cfg: dict) -> set[str]:
    """Derive required EAI domains from a parsed YAML config.

    When custom mirrors are configured, the public upstream domains are
    replaced with the mirror host(s).  This dramatically simplifies the
    EAI for air-gapped / Artifactory / Nexus environments.
    """
    mirrors = _get_mirrors(cfg)
    has_mirrors = any(mirrors.get(k) for k in (
        "conda_channel", "pypi_index", "cran_mirror", "micromamba_url"))

    if has_mirrors:
        domains: set[str] = set()
        for key in ("conda_channel", "pypi_index", "cran_mirror", "micromamba_url"):
            url = mirrors.get(key, "")
            if url:
                host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
                if host:
                    domains.add(host)
        # Tarballs may also point to the mirror
        tarballs = cfg.get("languages", {}).get("r", {}).get("tarballs", {}) or {}
        for url in tarballs.values():
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
                if host:
                    domains.add(host)
        return domains

    domains = set(SHARED_DOMAINS + TOOLKIT_DOMAINS)
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


# ==========================================================================
# Section 5: EAI SQL introspection helpers
# ==========================================================================

def _parse_host_list(raw: str) -> set[str]:
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
    try:
        rows = session.sql(f"DESCRIBE NETWORK RULE {rule_name}").collect()
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                d = {str(i): row[i] for i in range(len(row))}
            upper_d = {str(k).upper(): v for k, v in d.items()}

            if "VALUE_LIST" in upper_d:
                raw = str(upper_d["VALUE_LIST"])
                if raw and "." in raw:
                    return _parse_host_list(raw)

            prop_name = ""
            for k in ("NAME", "PROPERTY", "PROPERTY_NAME"):
                if k in upper_d:
                    prop_name = str(upper_d[k]).upper()
                    break
            if not any(kw in prop_name for kw in ("VALUE_LIST", "HOST_PORT", "VALUE")):
                continue
            raw = ""
            for k in ("VALUE", "PROPERTY_VALUE", "PROPERTY_DEFAULT"):
                if k in upper_d and upper_d[k]:
                    candidate = str(upper_d[k])
                    if "." in candidate:
                        raw = candidate
                        break
            if raw:
                return _parse_host_list(raw)
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


# ==========================================================================
# Section 6: EAI discovery (multi-tier)
# ==========================================================================

def _hint_eais_from_settings() -> list[str]:
    """Best-effort: read EAI names from .snowflake/settings.json.

    This file is a private implementation detail -- created lazily
    and NOT guaranteed to exist.
    """
    candidates = [os.path.join(os.getcwd(), ".snowflake", "settings.json")]
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
            svc = data.get("notebookSettings", {}).get("serviceDefaults", {})
            raw = svc.get("externalAccessIntegrations", [])
            names = [name.upper() for name in raw if name]
            if names:
                return names
        except Exception:
            continue
    return []


def _discover_eais_via_service(session) -> list[str]:
    """Get EAIs attached to this service via DESC SERVICE.

    Only works when SNOWFLAKE_SERVICE_NAME is set (guaranteed in
    non-interactive / scheduled notebook runs, not in interactive).
    Returns the exact EAIs attached to *this* service, which is more
    precise than SHOW INTEGRATIONS (which lists all visible to the role).
    """
    svc = os.environ.get("SNOWFLAKE_SERVICE_NAME")
    if not svc:
        return []
    try:
        db = (session.get_current_database() or "").replace('"', '')
        schema = (session.get_current_schema() or "").replace('"', '')
        if not db or not schema:
            return []
        fqn = f"{db}.{schema}.{svc}"
        rows = session.sql(f"DESC SERVICE {fqn}").collect()
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                continue
            upper_d = {str(k).upper(): v for k, v in d.items()}
            prop = str(upper_d.get("PROPERTY", "")).upper()
            if "EXTERNAL_ACCESS_INTEGRATIONS" in prop:
                raw = str(upper_d.get("VALUE", ""))
                return [
                    name.strip(" '\"")
                    for name in raw.strip("()").split(",")
                    if name.strip(" '\"")
                ]
    except Exception:
        pass
    return []


def _discover_eais_via_sql(session) -> list[str]:
    try:
        rows = session.sql("SHOW EXTERNAL ACCESS INTEGRATIONS").collect()
        names = []
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                continue
            upper_d = {str(k).upper(): v for k, v in d.items()}
            if str(upper_d.get("ENABLED", "")).lower() not in ("true", "1"):
                continue
            name = str(upper_d.get("NAME", "")).upper()
            if name:
                names.append(name)
        return names
    except Exception:
        return []


def _get_rule_type(session, rule_name: str) -> str:
    """Return the TYPE of a network rule (e.g. 'HOST_PORT', 'IPV4')."""
    try:
        rows = session.sql(f"DESCRIBE NETWORK RULE {rule_name}").collect()
        for row in rows:
            try:
                d = row.as_dict()
            except Exception:
                continue
            upper_d = {str(k).upper(): v for k, v in d.items()}
            prop = str(upper_d.get("PROPERTY", upper_d.get("NAME", ""))).upper()
            if "TYPE" in prop and "HOST" not in prop and "VALUE" not in prop:
                return str(upper_d.get("VALUE", upper_d.get(
                    "PROPERTY_VALUE", ""))).upper().strip()
    except Exception:
        pass
    return ""


def _is_open_eai(session, eai_name: str) -> bool:
    """Check whether an EAI is effectively open (allows all egress).

    Detects two patterns per Snowflake docs:
      - TYPE=IPV4  with VALUE_LIST containing '0.0.0.0/0'
      - TYPE=HOST_PORT with VALUE_LIST containing '0.0.0.0:port'
    """
    rules = _get_eai_rule_names(session, eai_name)
    for rule_name in rules:
        try:
            rule_type = _get_rule_type(session, rule_name)
            rows = session.sql(f"DESCRIBE NETWORK RULE {rule_name}").collect()
            for row in rows:
                try:
                    d = row.as_dict()
                except Exception:
                    d = {str(i): row[i] for i in range(len(row))}
                for v in d.values():
                    s = str(v)
                    if rule_type == "IPV4" and "0.0.0.0/0" in s:
                        return True
                    if "0.0.0.0/0" in s:
                        return True
                    if "0.0.0.0:" in s:
                        return True
        except Exception:
            continue
    return False


def _collect_all_eai_domains(session, eai_names: list[str]) -> dict[str, set[str]]:
    """For each EAI, collect its domains. Returns {eai_name: {domains}}."""
    result: dict[str, set[str]] = {}
    for eai in eai_names:
        domains: set[str] = set()
        for rule in _get_eai_rule_names(session, eai):
            domains |= _get_rule_domains(session, rule)
        result[eai] = domains
    return result


def _domain_coverage_map(
    required: set[str],
    eai_domains: dict[str, set[str]],
) -> tuple[set[str], dict[str, str]]:
    """Return (missing_domains, {covered_domain: eai_name})."""
    covered: dict[str, str] = {}
    for eai, doms in eai_domains.items():
        for d in doms:
            if d in required and d not in covered:
                covered[d] = eai
    missing = required - set(covered.keys())
    return missing, covered


def _print_annotated_sql(
    rule_name: str,
    missing: set[str],
    covered: dict[str, str],
    eai_name: str,
    quiet: bool = False,
    auth_secret: str = "",
):
    """Print CREATE OR REPLACE with covered domains commented out."""
    lines = [
        f"  CREATE OR REPLACE NETWORK RULE {rule_name}",
        f"    MODE = EGRESS",
        f"    TYPE = HOST_PORT",
        f"    VALUE_LIST = (",
    ]
    for d in sorted(missing):
        lines.append(f"      '{d}',")
    if covered:
        lines.append(f"      -- Already covered by existing EAI(s):")
        for d in sorted(covered):
            lines.append(f"      -- '{d}'  (via {covered[d]})")
    lines.append(f"    );")
    lines.append(f"")
    lines.append(f"  CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {eai_name}")
    lines.append(f"    ALLOWED_NETWORK_RULES = ({rule_name})")
    if auth_secret:
        lines.append(f"    ALLOWED_AUTHENTICATION_SECRETS = ({auth_secret})")
    lines.append(f"    ENABLED = TRUE;")

    header = f"\n  EAI domain summary ({len(missing)} new, {len(covered)} already covered):"
    _log(header, quiet=quiet)
    _log("  " + "-" * 60, quiet=quiet)
    for ln in lines:
        _log(ln, quiet=quiet)
    _log("  " + "-" * 60, quiet=quiet)


def _print_attach_instructions(eai_name: str, quiet: bool = False):
    _log(f"\n  Attach '{eai_name}' to your notebook service:", quiet=quiet)
    _log(f"    1. Click 'Connected' (top-left toolbar)", quiet=quiet)
    _log(f"    2. Hover over service name > Edit", quiet=quiet)
    _log(f"    3. Scroll to External Access", quiet=quiet)
    _log(f"    4. Toggle ON '{eai_name}' > Save", quiet=quiet)
    _log(f"    5. Service restarts automatically", quiet=quiet)


# ==========================================================================
# Section 7: ensure_eai -- EAI validation and creation
# ==========================================================================

def _test_domain_reachability(
    domains: set[str],
) -> tuple[set[str], set[str]]:
    """Test DNS resolution for each domain (parallel).

    Returns (reachable, unreachable).  In SPCS containers, network rules
    control DNS -- if a domain isn't in an attached EAI, DNS won't resolve.
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def _check(domain):
        try:
            socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
            return (domain, True)
        except (socket.gaierror, OSError):
            return (domain, False)

    reachable: set[str] = set()
    unreachable: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(len(domains), 10)) as pool:
        for domain, ok in pool.map(_check, domains):
            (reachable if ok else unreachable).add(domain)
    return reachable, unreachable


def _find_target_eai(session, managed, supplementary_name) -> str | None:
    """Find the target EAI name to inspect/modify.

    Priority: managed (from config) > convention name > None.
    Returns the name only if it exists (visible to role via SHOW INTEGRATIONS).
    """
    sql_eais = _discover_eais_via_sql(session)
    upper_map = {e.upper(): e for e in sql_eais}
    if managed and managed.upper() in upper_map:
        return upper_map[managed.upper()]
    if supplementary_name.upper() in upper_map:
        return upper_map[supplementary_name.upper()]
    return None


def ensure_eai(
    session=None,
    config: str | None = None,
    eai_managed: Optional[str] = None,
    quiet: bool = False,
) -> dict:
    """Validate EAI domains and create/modify as needed.

    Strategy:
    1. If a named EAI is specified (config or convention): introspect its
       domains via SQL (~0.5s).  If all required domains are present,
       return immediately (fast path).  If missing, ALTER the network rule.
    2. If no named EAI is found: test DNS reachability of required domains
       (~3s parallel).  If all resolve, return (some unknown EAI covers us).
       If unreachable, CREATE a supplementary EAI.
    3. After any ALTER/CREATE: re-test previously-missing domains via DNS.
       If still unreachable, warn that the EAI is likely not attached.

    Parameters
    ----------
    session : Snowpark session (auto-detected if None)
    config : path to YAML config file
    eai_managed : name of an EAI to modify (from config or parameter)
    quiet : suppress verbose output
    """
    if session is None:
        from snowflake.snowpark.context import get_active_session
        session = get_active_session()

    cfg = _read_config(config)
    eai_section = cfg.get("eai", {}) or {}
    managed = eai_managed or eai_section.get("managed")
    supplementary_name = (
        eai_section.get("supplementary_name", DEFAULT_SUPPLEMENTARY_NAME)
    ).upper()
    rule_suffix = eai_section.get("network_rule", DEFAULT_RULE_NAME).upper()
    mirrors = _get_mirrors(cfg)
    _auth_secret = mirrors.get("auth_secret", "")

    required = _domains_from_config(cfg)
    _log(f"  Required domains: {required}", quiet=quiet)

    current_role = ""
    try:
        current_role = (session.get_current_role() or "").replace('"', '')
    except Exception:
        pass

    result = {
        "action": "no_change",
        "eai_name": managed or supplementary_name,
        "domains_added": [],
    }

    # -- Path A: Named EAI (fast metadata check) ---------------------------
    target_eai = _find_target_eai(session, managed, supplementary_name)

    if target_eai:
        target_upper = target_eai.upper()
        _log(f"  EAI '{target_upper}' found. Checking domains...", quiet=quiet)

        # Check for open EAI first (skip domain enumeration)
        if _is_open_eai(session, target_upper):
            _log(f"  EAI '{target_upper}' allows all egress (open).",
                 quiet=quiet)
            return {"action": "open_eai", "eai_name": target_upper,
                    "domains_added": []}

        # Introspect domains from this EAI's network rules
        eai_domains = _collect_all_eai_domains(session, [target_upper])
        covered = set()
        for doms in eai_domains.values():
            covered |= doms
        _log(f"  Covered domains: {covered}", quiet=quiet)
        missing = required - covered

        if not missing:
            _log(f"  EAI '{target_upper}' has all {len(required)} required "
                 f"domains.", quiet=quiet)
            result["eai_name"] = target_upper
            return result

        _log(f"  EAI '{target_upper}': {len(required) - len(missing)}"
             f"/{len(required)} domains present, {len(missing)} missing.",
             quiet=quiet)
        for d in sorted(missing):
            _log(f"    x  {d}", quiet=quiet)

        # ALTER the named EAI's network rule to add missing domains
        rules = _get_eai_rule_names(session, target_upper)
        actual_rule = rules[0] if rules else rule_suffix
        current = _get_rule_domains(session, actual_rule)
        merged = sorted(current | required)
        host_list = ", ".join(f"'{h}'" for h in merged)
        alter_sql = (
            f"ALTER NETWORK RULE {actual_rule} SET VALUE_LIST = ({host_list})"
        )
        try:
            session.sql(alter_sql).collect()
            added = sorted(missing)
            _log(f"  Updated '{target_upper}': added {len(added)} domain(s).",
                 quiet=quiet)
            for d in added:
                _log(f"    + {d}", quiet=quiet)
            result["action"] = "updated"
            result["eai_name"] = target_upper
            result["domains_added"] = added
            _, coverage_map = _domain_coverage_map(required, eai_domains)
            _print_annotated_sql(
                actual_rule, missing, coverage_map, target_upper,
                quiet=quiet, auth_secret=_auth_secret)
            # Re-test via DNS to confirm the change is live
            _, still_bad = _test_domain_reachability(missing)
            if still_bad:
                _log(f"\n  WARNING: {len(still_bad)} domain(s) still "
                     f"unreachable after update.", quiet=quiet)
                for d in sorted(still_bad):
                    _log(f"    x  {d}", quiet=quiet)
                _log("  The EAI may not be attached to this Notebook "
                     "service.", quiet=quiet)
                _print_attach_instructions(target_upper, quiet=quiet)
            else:
                _log("  Verified: all domains now reachable.", quiet=quiet)
            return result
        except Exception as exc:
            _log(f"  ALTER failed: {exc}", quiet=quiet)
            _log("  Falling back to supplementary EAI creation...",
                 quiet=quiet)

    # -- Path B: No named EAI -- DNS reachability test ---------------------
    if not target_eai:
        _log(f"  No named EAI found. Testing {len(required)} domains via "
             f"DNS...", quiet=quiet)
        reachable, unreachable = _test_domain_reachability(required)

        if not unreachable:
            _log(f"  All {len(required)} domains reachable.", quiet=quiet)
            return result

        _log(f"  {len(reachable)} reachable, {len(unreachable)} "
             f"unreachable:", quiet=quiet)
        for d in sorted(unreachable):
            _log(f"    x  {d}", quiet=quiet)
        missing = unreachable
    else:
        # Path A ALTER failed -- missing was already computed above
        pass

    # -- Fix phase: CREATE supplementary EAI with missing domains ----------
    supp_rule = rule_suffix
    if managed:
        supp_rule = supplementary_name.replace("_EAI", "_EGRESS")

    missing_sorted = sorted(missing)
    host_list = ", ".join(f"'{h}'" for h in missing_sorted)

    create_rule = (
        f"CREATE OR REPLACE NETWORK RULE {supp_rule}\n"
        f"  MODE = EGRESS\n"
        f"  TYPE = HOST_PORT\n"
        f"  VALUE_LIST = ({host_list})"
    )
    _auth_clause = (
        f"\n  ALLOWED_AUTHENTICATION_SECRETS = ({_auth_secret})"
        if _auth_secret else ""
    )
    create_eai = (
        f"CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION {supplementary_name}\n"
        f"  ALLOWED_NETWORK_RULES = ({supp_rule}){_auth_clause}\n"
        f"  ENABLED = TRUE"
    )
    grant = ""
    if current_role:
        grant = (
            f"GRANT USAGE ON INTEGRATION {supplementary_name} "
            f"TO ROLE {current_role}"
        )

    try:
        session.sql(create_rule).collect()
        session.sql(create_eai).collect()
        if grant:
            session.sql(grant).collect()
        _log(f"  Created EAI '{supplementary_name}' with "
             f"{len(missing_sorted)} domain(s).", quiet=quiet)
        result["action"] = "created"
        result["eai_name"] = supplementary_name
        result["domains_added"] = missing_sorted

        # Re-test to confirm the EAI is attached and working
        _, still_bad = _test_domain_reachability(missing)
        if still_bad:
            _log(f"\n  WARNING: {len(still_bad)} domain(s) still unreachable "
                 f"after creating EAI.", quiet=quiet)
            _log("  The EAI is likely not attached to this Notebook "
                 "service.", quiet=quiet)
            _print_attach_instructions(supplementary_name, quiet=quiet)
        else:
            _log("  Verified: all domains now reachable.", quiet=quiet)
    except Exception as exc:
        _log(f"  CREATE failed (insufficient privileges?): {exc}", quiet=quiet)
        result["action"] = "print_sql"

    eai_domains = _collect_all_eai_domains(session, [target_eai or ""])
    _, coverage_map = _domain_coverage_map(required, eai_domains)
    _print_annotated_sql(
        supp_rule, missing, coverage_map, supplementary_name,
        quiet=quiet, auth_secret=_auth_secret)

    if result["action"] == "print_sql":
        _log("\nCould not create EAI (insufficient privileges).", quiet=quiet)
        _log("Share the SQL above with your Snowflake admin.", quiet=quiet)
        _print_attach_instructions(supplementary_name, quiet=quiet)

    return result


# ==========================================================================
# Section 8: Bootstrap sfnb-multilang
# ==========================================================================

def _detect_monorepo():
    if os.environ.get("SFNB_PUBLIC_MODE"):
        return None
    d = os.getcwd()
    for _ in range(10):
        if os.path.isfile(os.path.join(d, ".monorepo")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _find_local_src():
    """Find sfnb_multilang source tree relative to this file or cwd.

    Walks up from both anchors looking for src/sfnb_multilang/__init__.py.
    Works when sfnb_setup.py lives inside the snowflake-notebook-multilang
    repo (e.g. Workspace created from the public repo) without needing
    network access.
    """
    anchors = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    for anchor in anchors:
        d = anchor
        for _ in range(10):
            candidate = os.path.join(d, "src", "sfnb_multilang", "__init__.py")
            if os.path.isfile(candidate):
                return os.path.join(d, "src")
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def _find_config_yaml() -> str | None:
    """Find a *_config.yaml next to this script (best-effort for bootstrap)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    import glob as _glob
    candidates = sorted(_glob.glob(os.path.join(script_dir, "*_config.yaml")))
    return candidates[0] if candidates else None


def _build_eai_sql(domains: set[str], auth_secret: str = "") -> str:
    """Build CREATE NETWORK RULE + EAI SQL from a set of domains."""
    host_list = "\n".join(
        f"      '{d}'," if i < len(sorted(domains)) - 1 else f"      '{d}'"
        for i, d in enumerate(sorted(domains))
    )
    auth_line = ""
    if auth_secret:
        auth_line = f"\n    ALLOWED_AUTHENTICATION_SECRETS = ({auth_secret})"
    return (
        f"  CREATE OR REPLACE NETWORK RULE {DEFAULT_RULE_NAME}\n"
        f"    MODE = EGRESS\n"
        f"    TYPE = HOST_PORT\n"
        f"    VALUE_LIST = (\n"
        f"{host_list}\n"
        f"    );\n"
        f"\n"
        f"  CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION\n"
        f"    {DEFAULT_SUPPLEMENTARY_NAME}\n"
        f"    ALLOWED_NETWORK_RULES = ({DEFAULT_RULE_NAME}){auth_line}\n"
        f"    ENABLED = TRUE;"
    )


def _bootstrap():
    root = _detect_monorepo()
    if root:
        os.environ.setdefault("MONOREPO_ROOT", root)
        src = os.path.join(root, "snowflake-notebook-multilang", "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        for pkg, var in [("snowflakeR", "SNOWFLAKER_PATH"),
                         ("RSnowflake", "RSNOWFLAKE_PATH")]:
            pkg_dir = os.path.join(root, pkg)
            if os.path.isdir(pkg_dir):
                os.environ.setdefault(var, pkg_dir)

    try:
        _found = importlib.util.find_spec("sfnb_multilang") is not None
    except OSError:
        _found = False
    if not _found:
        local_src = _find_local_src()
        if local_src:
            if local_src not in sys.path:
                sys.path.insert(0, local_src)
        else:
            # Check for custom PyPI mirror from any config YAML nearby
            _boot_cfg_path = _find_config_yaml()
            _boot_cfg = _read_config(_boot_cfg_path) if _boot_cfg_path else {}
            _boot_mirrors = _apply_mirror_auth(_get_mirrors(_boot_cfg))
            _boot_pip_flags = _pip_index_flags(_boot_mirrors)

            _GITHUB_URL = (
                "sfnb-multilang @ https://github.com/Snowflake-Labs/"
                "snowflake-notebook-multilang/archive/refs/heads/main.zip"
            )
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-q",
                     _GITHUB_URL] + _boot_pip_flags)
            except subprocess.CalledProcessError:
                cfg_path = _find_config_yaml()
                cfg = _read_config(cfg_path) if cfg_path else {}
                domains = _domains_from_config(cfg)
                _boot_auth = _get_mirrors(cfg).get("auth_secret", "")
                eai_sql = _build_eai_sql(domains, auth_secret=_boot_auth)

                print(
                    "\n"
                    "============================================================\n"
                    "  ERROR: Cannot install sfnb-multilang\n"
                    "============================================================\n"
                    "\n"
                    "  The sfnb_multilang package was not found locally and\n"
                    "  could not be downloaded from GitHub (no network access).\n"
                    "\n"
                    "  This usually means your Workspace Notebook service does\n"
                    "  not have an External Access Integration (EAI) attached.\n"
                    "\n"
                    "  To fix this, run the following SQL in a Snowflake\n"
                    "  worksheet or any SQL cell:\n"
                    "\n"
                    "  ---- copy from here ----\n"
                    "\n"
                    f"{eai_sql}\n"
                    "\n"
                    "  ---- copy to here ----\n"
                    "\n"
                    "  Then:\n"
                    "    1. Open your Notebook in Snowsight\n"
                    "    2. Click  ...  > Notebook settings > External access\n"
                    f"    3. Toggle {DEFAULT_SUPPLEMENTARY_NAME} on\n"
                    "    4. Restart the kernel and re-run this cell\n"
                    "\n"
                    "  Alternatively, create your Workspace from the\n"
                    "  snowflake-notebook-multilang repo so the package source\n"
                    "  is available locally (no EAI needed for bootstrap).\n"
                    "\n"
                    "============================================================\n",
                    file=sys.stderr,
                )
                raise SystemExit(1)


_bootstrap()

from sfnb_multilang import install  # noqa: E402


# ==========================================================================
# Section 9: Language installation helpers
# ==========================================================================

def install_r(**kwargs):
    """Install R and register the %%R magic."""
    kwargs.setdefault("languages", ["r"])
    install(**kwargs)
    try:
        from sfnb_multilang.helpers.r_helpers import setup_r_environment
    except ImportError:
        from r_helpers import setup_r_environment
    setup_r_environment()


# ==========================================================================
# Section 10: R package installer (tarball / URL / pak)
# ==========================================================================

_GITHUB_FALLBACKS = {
    "snowflakeR": "Snowflake-Labs/snowflakeR",
    "RSnowflake": "Snowflake-Labs/RSnowflake",
}


def _resolve_tarball(
    pkg: str,
    src: str | None,
    quiet: bool = False,
    ssl_cert_path: str = "",
) -> str | None:
    if src:
        if src.startswith(("http://", "https://")):
            from urllib.parse import urlparse, urlunparse, unquote
            import base64

            parsed = urlparse(src)
            headers = {"User-Agent": "sfnb-multilang"}
            download_url = src
            if parsed.username:
                raw_user = unquote(parsed.username)
                raw_pass = unquote(parsed.password or "")
                token = base64.b64encode(f"{raw_user}:{raw_pass}".encode()).decode()
                headers["Authorization"] = f"Basic {token}"
                clean_netloc = parsed.hostname
                if parsed.port:
                    clean_netloc += f":{parsed.port}"
                download_url = urlunparse(parsed._replace(netloc=clean_netloc))

            dest = os.path.join(tempfile.gettempdir(), os.path.basename(parsed.path))
            _log(f"  {pkg}: downloading {_mask_url_credentials(src)}", quiet=quiet)
            try:
                req = urllib.request.Request(download_url, headers=headers)
                ctx = None
                if ssl_cert_path and os.path.isfile(ssl_cert_path):
                    import ssl
                    ctx = ssl.create_default_context(cafile=ssl_cert_path)
                with urllib.request.urlopen(req, context=ctx) as resp:
                    with open(dest, "wb") as f:
                        f.write(resp.read())
                return dest
            except Exception as exc:
                _log(f"  {pkg}: URL download failed ({exc}), "
                     "trying local search...", quiet=quiet)
        elif os.path.exists(src):
            return src
        else:
            _log(f"  {pkg}: WARNING configured path not found: {src}", quiet=quiet)

    hits = sorted(glob.glob(f"**/{pkg}_*.tar.gz", recursive=True))
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    _log(f"  {pkg}: found {len(hits)} tarballs, using newest", quiet=quiet)
    return hits[-1]


def _r_install(path: str):
    from rpy2.robjects import r as R
    R(f'install.packages("{path}", repos = NULL, type = "source")')


def _r_pak_install(repo: str, cran_mirror: str = "https://cloud.r-project.org"):
    from rpy2.robjects import r as R
    R(f'options(repos = c(CRAN = "{cran_mirror}"), '
      f'pkg.sysreqs = FALSE)')
    R('if (!requireNamespace("pak", quietly = TRUE)) '
      'install.packages("pak", type = "source", quiet = TRUE)')
    R(f'pak::pak("{repo}", ask = FALSE, upgrade = FALSE)')


def _r_pkg_version(pkg: str) -> str:
    from rpy2.robjects import r as R
    try:
        return str(R(f'as.character(packageVersion("{pkg}"))')[0])
    except Exception:
        return "NOT FOUND"


def install_r_packages(
    config: str | None = None,
    packages: list[str] | None = None,
    quiet: bool = False,
):
    """Install R packages from tarballs (URL/local/search) with pak fallback."""
    if packages is None:
        packages = ["snowflakeR", "RSnowflake"]

    cfg = _read_config(config)
    mirrors = _apply_mirror_auth(_get_mirrors(cfg))
    cran_mirror = mirrors.get("cran_mirror") or "https://cloud.r-project.org"
    ssl_cert = mirrors.get("ssl_cert_path", "")
    pip_flags = _pip_index_flags(mirrors)

    tarballs: dict[str, str] = (
        cfg.get("languages", {}).get("r", {}).get("tarballs")
    ) or {}

    auth_secret = mirrors.get("auth_secret", "")
    if auth_secret:
        user, pwd = _read_mirror_credentials(auth_secret)
        if user:
            tarballs = {k: _inject_auth_url(v, user, pwd) for k, v in tarballs.items()}

    core = [p for p in packages if p in _GITHUB_FALLBACKS]
    extras = [p for p in tarballs if p not in packages]

    for pkg in core:
        path = _resolve_tarball(pkg, tarballs.get(pkg), quiet=quiet, ssl_cert_path=ssl_cert)
        if path:
            _log(f"  {pkg} <- {path}", quiet=quiet)
            _r_install(path)
        elif pkg in _GITHUB_FALLBACKS:
            _log(f"  {pkg}: no tarball -- falling back to GitHub", quiet=quiet)
            _r_pak_install(_GITHUB_FALLBACKS[pkg], cran_mirror=cran_mirror)
        else:
            _log(f"  {pkg}: WARNING could not resolve", quiet=quiet)

    for pkg in extras:
        path = _resolve_tarball(pkg, tarballs.get(pkg), quiet=quiet, ssl_cert_path=ssl_cert)
        if path:
            _log(f"  {pkg} <- {path}", quiet=quiet)
            _r_install(path)
        else:
            _log(f"  {pkg}: WARNING could not resolve tarball", quiet=quiet)

    # Install pip packages required by R packages (e.g. nevergrad for Robyn)
    # Skip packages already installed by Phase 4 of the toolkit installer.
    pip_pkgs: list = (
        cfg.get("languages", {}).get("r", {}).get("pip_packages")
    ) or []
    if pip_pkgs:
        import importlib.util
        missing = [p for p in pip_pkgs
                   if importlib.util.find_spec(p.split("[")[0]) is None]
        if missing:
            _log("\nPip packages (R dependencies):", quiet=quiet)
            for pip_pkg in missing:
                _log(f"  pip install {pip_pkg}", quiet=quiet)
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-q", pip_pkg]
                    + pip_flags)

    _log("\nInstalled versions:", quiet=quiet)
    for pkg in core + extras:
        _log(f"  {pkg} {_r_pkg_version(pkg)}", quiet=quiet)


# ==========================================================================
# Section 11: setup_notebook -- all-in-one entry point
# ==========================================================================

def setup_notebook(
    config: str | None = None,
    packages: list[str] | None = None,
    languages: list[str] | None = None,
    quiet: bool = False,
) -> dict:
    """Single-cell notebook bootstrap.

    1. Read config YAML (context + eai + languages + tarballs)
    2. Set session context (from config or session defaults)
    3. Discover/validate EAI domains; create supplementary if needed
    4. Bootstrap sfnb-multilang (pip install if needed)
    5. Install language runtime (R/Scala/Julia) + register magics
    6. Install language packages (tarballs/pak)
    7. Print summary + write .sfnb_setup.log

    Parameters
    ----------
    config : path to YAML config file (optional)
    packages : R packages to install, e.g. ["snowflakeR"] (optional)
    languages : languages to install, default ["r"]
    quiet : if True, suppress progress output (summary + errors still shown)
    """
    global _LOG_LINES
    _LOG_LINES = []
    t0 = time.monotonic()

    cfg = _read_config(config)
    mirrors = _get_mirrors(cfg)
    if any(mirrors.get(k) for k in ("conda_channel", "pypi_index", "cran_mirror")):
        _log("Custom mirrors configured:", quiet=quiet)
        for k, v in mirrors.items():
            if v and k != "auth_secret":
                _log(f"  {k}: {v}", quiet=quiet)

    # Resolve mirror authentication and inject credentials into URLs
    if mirrors.get("auth_secret"):
        _log(f"  auth_secret: {mirrors['auth_secret']}", quiet=quiet)
        mirrors = _apply_mirror_auth(mirrors)
        if any("@" in mirrors.get(k, "") for k in
               ("conda_channel", "pypi_index", "cran_mirror", "micromamba_url")):
            _log("  Mirror authentication: credentials loaded", quiet=quiet)

    # Export Model Registry conda channel policy as env vars so that
    # sfr_model_registry() / sfr_log_model() pick it up automatically.
    _apply_registry_env(cfg, quiet=quiet)

    # -- 1. Session context ------------------------------------------------
    from snowflake.snowpark.context import get_active_session
    session = get_active_session()
    effective_ctx = _set_session_context(session, cfg, quiet=quiet)

    # -- 2. EAI validation -------------------------------------------------
    _log("\n--- EAI validation ---", quiet=quiet)
    eai_result = ensure_eai(session=session, config=config, quiet=quiet)
    eai_action = eai_result.get("action", "no_change")

    if eai_action == "print_sql":
        _log("\nSetup paused: EAI requires admin action (see SQL above).",
             quiet=False)
        _flush_log()
        return {"status": "eai_blocked", "eai": eai_result}

    # -- 3. Install language runtime ---------------------------------------
    _log("\n--- Language runtime ---", quiet=quiet)
    langs = languages
    if langs is None:
        lang_cfg = cfg.get("languages", {})
        langs = [k for k, v in lang_cfg.items()
                 if isinstance(v, dict) and v.get("enabled")]
        if not langs:
            langs = ["r"]

    install_kwargs = {"languages": langs}
    if config:
        install_kwargs["config"] = config

    if "r" in langs:
        t_r = time.monotonic()
        install_r(**install_kwargs)
        _log(f"  R runtime: {time.monotonic() - t_r:.0f}s", quiet=quiet)

        # -- 4. Install R packages -----------------------------------------
        if packages is not None or cfg.get("languages", {}).get("r", {}).get("tarballs"):
            _log("\n--- R packages ---", quiet=quiet)
            t_pkg = time.monotonic()
            install_r_packages(config=config, packages=packages, quiet=quiet)
            _log(f"  R packages: {time.monotonic() - t_pkg:.0f}s", quiet=quiet)

            # Pre-warm heavy ML imports so first user cell isn't slow
            _prewarm_ml_imports(quiet=quiet, mirrors=mirrors)

            # Set SPCS OAuth env vars for RSnowflake DBI connectivity
            pkgs = packages or ["snowflakeR", "RSnowflake"]
            if "RSnowflake" in pkgs:
                _strip = lambda s: (s or "").replace('"', '')
                os.environ["SNOWFLAKE_ACCOUNT"] = _strip(
                    session.get_current_account())
                os.environ["SNOWFLAKE_USER"] = session.sql(
                    "SELECT CURRENT_USER()").collect()[0][0]
                os.environ["SNOWFLAKE_DATABASE"] = _strip(
                    session.get_current_database())
                os.environ["SNOWFLAKE_SCHEMA"] = _strip(
                    session.get_current_schema())
                os.environ["SNOWFLAKE_WAREHOUSE"] = _strip(
                    session.get_current_warehouse())
                os.environ["SNOWFLAKE_ROLE"] = _strip(
                    session.get_current_role())
    else:
        install(**install_kwargs)

    # -- 5. Summary --------------------------------------------------------
    elapsed = time.monotonic() - t0
    _log(f"\n{'=' * 60}", quiet=False)
    _log(f"  Setup complete in {elapsed:.0f}s", quiet=False)
    _log(f"  Context: {effective_ctx['database']}.{effective_ctx['schema']}", quiet=False)
    _log(f"  EAI: {eai_result.get('eai_name', '?')} ({eai_action})", quiet=False)
    _log(f"  Languages: {', '.join(langs)}", quiet=False)
    if packages:
        _log(f"  R packages: {', '.join(packages)}", quiet=False)
    _log(f"{'=' * 60}", quiet=False)

    _flush_log()

    log_path = _find_log_path()
    if log_path:
        print(f"  Log: {log_path}")

    return {
        "status": "ready",
        "elapsed_s": round(elapsed, 1),
        "context": effective_ctx,
        "eai": eai_result,
        "languages": langs,
    }
