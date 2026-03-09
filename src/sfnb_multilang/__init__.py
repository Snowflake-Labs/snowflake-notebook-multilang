"""Snowflake Notebook Multi-Language Toolkit.

Install and manage R, Scala/Java, and Julia in Snowflake Workspace Notebooks.

Quick start::

    from sfnb_multilang import install
    install(languages=["r", "scala"])

For EAI setup::

    from sfnb_multilang import apply_eai, generate_eai_sql
    apply_eai(session, languages=["r", "scala"])
"""

from __future__ import annotations

__version__ = "0.1.0"


def install(
    config: str | None = None,
    languages: list[str] | None = None,
    **kwargs,
):
    """Install language runtimes into the current Workspace Notebook.

    Args:
        config: Path to a YAML config file, or None for defaults.
        languages: List of languages to enable (e.g. ["r", "scala"]).
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

    setup_logging(level=cfg.logging.level, log_file=cfg.logging.log_file or None)

    installer = Installer(cfg)
    return installer.install()


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
