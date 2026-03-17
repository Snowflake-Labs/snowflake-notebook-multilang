"""Snowflake Notebook Multi-Language Toolkit.

Install and manage R, Scala/Java, and Julia in Snowflake Workspace Notebooks.

Quick start::

    from sfnb_multilang import install
    install(languages=["r", "scala"])

For EAI setup::

    from sfnb_multilang import ensure_eai
    ensure_eai(session, config="my_config.yaml")

    # Or the older CREATE OR REPLACE approach:
    from sfnb_multilang import apply_eai, generate_eai_sql
    apply_eai(session, languages=["r", "scala"])
"""

from __future__ import annotations

__version__ = "0.1.0"


def install(
    config: str | None = None,
    languages: list[str] | None = None,
    quiet: bool = False,
    **kwargs,
):
    """Install language runtimes into the current Workspace Notebook.

    Args:
        config: Path to a YAML config file, or None for defaults.
        languages: List of languages to enable (e.g. ["r", "scala"]).
        quiet: If True, suppress INFO messages and only show a final
            summary line.  Errors and warnings are always shown.
        **kwargs: Additional overrides (r_adbc, r_duckdb, verbose, force).
    """
    from .config import ToolkitConfig, apply_cli_overrides, load_config
    from .installer import Installer
    from .logging_config import setup_logging

    if config:
        cfg = load_config(config)
    else:
        cfg = ToolkitConfig()

    overrides = dict(kwargs)
    if languages:
        overrides["languages"] = languages
    cfg = apply_cli_overrides(cfg, **overrides)

    log_level = "WARNING" if quiet else cfg.logging.level
    setup_logging(level=log_level, log_file=cfg.logging.log_file or None)

    installer = Installer(cfg)
    return installer.install(quiet=quiet)


def generate_eai_sql(
    config: str | None = None,
    account: str = "",
    languages: list[str] | None = None,
    **kwargs,
) -> str:
    """Generate EAI network rule SQL for the given configuration.

    Returns the SQL string.
    """
    from .config import ToolkitConfig, apply_cli_overrides, load_config
    from .network_rules import generate_network_rule_sql

    if config:
        cfg = load_config(config)
    else:
        cfg = ToolkitConfig()

    if languages:
        cfg = apply_cli_overrides(cfg, languages=languages, **kwargs)

    return generate_network_rule_sql(cfg, account=account)


def apply_eai(
    session,
    config: str | None = None,
    account: str = "",
    languages: list[str] | None = None,
    **kwargs,
) -> str:
    """Generate and execute EAI SQL via the Snowpark session.

    Returns the generated SQL string.
    """
    from .config import ToolkitConfig, apply_cli_overrides, load_config
    from .network_rules import apply_eai as _apply_eai

    if config:
        cfg = load_config(config)
    else:
        cfg = ToolkitConfig()

    if languages:
        cfg = apply_cli_overrides(cfg, languages=languages)

    return _apply_eai(session, config=cfg, account=account, **kwargs)


def ensure_eai(
    session,
    config: str | None = None,
    eai_name: str | None = None,
    rule_name: str | None = None,
    account: str = "",
    languages: list[str] | None = None,
    **kwargs,
) -> dict:
    """Ensure the EAI has all domains required by the configured languages.

    Introspects an existing EAI and merges in any missing domains.
    Creates the EAI if it doesn't exist.  Falls back to printing SQL
    if the caller lacks privileges.

    Returns a dict with ``action``, ``eai_name``, ``rule_name``,
    ``domains_added``, and ``sql``.
    """
    from .config import ToolkitConfig, apply_cli_overrides, load_config
    from .network_rules import ensure_eai as _ensure_eai

    if config:
        cfg = load_config(config)
    else:
        cfg = ToolkitConfig()

    if languages:
        cfg = apply_cli_overrides(cfg, languages=languages, **kwargs)

    return _ensure_eai(
        session, config=cfg, eai_name=eai_name,
        rule_name=rule_name, account=account,
        grant_to_role=kwargs.get("grant_to_role"),
    )
