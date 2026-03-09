"""Command-line interface for the multi-language toolkit."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import yaml

from .config import ToolkitConfig, apply_cli_overrides, config_to_dict, load_config
from .exceptions import ToolkitError
from .logging_config import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sfnb-setup",
        description="Multi-language setup for Snowflake Workspace Notebooks",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # -- install --
    p_install = sub.add_parser("install", help="Install language runtimes")
    p_install.add_argument("--config", "-c", default=None, help="YAML config file")
    p_install.add_argument("--r", action="store_true", help="Enable R")
    p_install.add_argument("--scala", action="store_true", help="Enable Scala/Java")
    p_install.add_argument("--julia", action="store_true", help="Enable Julia")
    p_install.add_argument("--all", action="store_true", help="Enable all languages")
    p_install.add_argument("--r-adbc", action="store_true", help="Enable R ADBC addon")
    p_install.add_argument("--r-duckdb", action="store_true", help="Enable R DuckDB addon")
    p_install.add_argument("--apply-eai", action="store_true", default=True,
                           help="Try to apply EAI before install (default: true)")
    p_install.add_argument("--no-eai", action="store_true",
                           help="Skip EAI setup")
    p_install.add_argument("--account", default="", help="Snowflake account for EAI")
    p_install.add_argument("--verbose", "-v", action="store_true")
    p_install.add_argument("--force", action="store_true", help="Force reinstall")
    p_install.add_argument("--dry-run", action="store_true",
                           help="Show what would be installed")

    # -- generate-eai --
    p_eai = sub.add_parser("generate-eai", help="Generate EAI SQL")
    p_eai.add_argument("--config", "-c", default=None, help="YAML config file")
    p_eai.add_argument("--r", action="store_true")
    p_eai.add_argument("--scala", action="store_true")
    p_eai.add_argument("--julia", action="store_true")
    p_eai.add_argument("--all", action="store_true")
    p_eai.add_argument("--r-adbc", action="store_true")
    p_eai.add_argument("--account", default="", help="Snowflake account")
    p_eai.add_argument("--output", "-o", default=None, help="Output file (default: stdout)")

    # -- validate --
    p_val = sub.add_parser("validate", help="Validate existing installation")
    p_val.add_argument("--config", "-c", default=None)

    # -- migrate-config --
    p_mig = sub.add_parser("migrate-config", help="Convert per-language YAMLs to unified")
    p_mig.add_argument("--r-config", default=None, help="R packages YAML")
    p_mig.add_argument("--scala-config", default=None, help="Scala packages YAML")
    p_mig.add_argument("--julia-config", default=None, help="Julia packages YAML")
    p_mig.add_argument("--output", "-o", default="config.yaml", help="Output YAML")

    return parser


def _resolve_languages(args) -> Optional[list[str]]:
    """Build language list from CLI flags."""
    if getattr(args, "all", False):
        return ["r", "scala", "julia"]
    langs = []
    if getattr(args, "r", False):
        langs.append("r")
    if getattr(args, "scala", False):
        langs.append("scala")
    if getattr(args, "julia", False):
        langs.append("julia")
    return langs or None


def _load_and_override(args) -> ToolkitConfig:
    """Load config from file (if given) and apply CLI overrides."""
    if args.config:
        cfg = load_config(args.config)
    else:
        cfg = ToolkitConfig()

    overrides = {}
    languages = _resolve_languages(args)
    if languages:
        overrides["languages"] = languages
    if getattr(args, "r_adbc", False):
        overrides["r_adbc"] = True
    if getattr(args, "r_duckdb", False):
        overrides["r_duckdb"] = True
    if getattr(args, "verbose", False):
        overrides["verbose"] = True
    if getattr(args, "force", False):
        overrides["force"] = True
    if getattr(args, "no_eai", False):
        overrides["apply_eai"] = False
    if getattr(args, "account", ""):
        overrides["account"] = args.account

    return apply_cli_overrides(cfg, **overrides)


def cmd_install(args) -> int:
    cfg = _load_and_override(args)
    setup_logging(level=cfg.logging.level, log_file=cfg.logging.log_file or None)

    if args.dry_run:
        from .installer import Installer
        installer = Installer(cfg)
        pkgs = installer._collect_conda_packages()
        pip_pkgs = installer._collect_pip_packages()
        print("Dry run -- would install:")
        print(f"  Languages: {', '.join(p.display_name for p in installer.plugins)}")
        print(f"  Conda packages: {' '.join(pkgs)}")
        print(f"  Pip packages: {' '.join(pip_pkgs)}")
        return 0

    from .installer import Installer
    installer = Installer(cfg)
    report = installer.install()
    return 0 if report.success else 1


def cmd_generate_eai(args) -> int:
    cfg = _load_and_override(args)
    from .network_rules import generate_network_rule_sql, export_eai_sql

    sql = generate_network_rule_sql(cfg, account=args.account)

    if args.output:
        export_eai_sql(cfg, output_path=args.output, account=args.account)
        print(f"SQL written to {args.output}")
    else:
        print(sql)

    return 0


def cmd_validate(args) -> int:
    cfg = _load_and_override(args) if args.config else ToolkitConfig()
    setup_logging(level="INFO")

    from .languages import get_enabled_plugins
    plugins = get_enabled_plugins(cfg)
    if not plugins:
        print("No languages enabled in config.")
        return 1

    from .shared.conda_env import get_env_prefix
    try:
        env_prefix = get_env_prefix(cfg.env_name)
    except FileNotFoundError:
        print(f"Environment '{cfg.env_name}' not found. Run install first.")
        return 1

    all_ok = True
    for plugin in plugins:
        result = plugin.validate_install(env_prefix, cfg)
        status = "OK" if result.success else "FAILED"
        print(f"  {plugin.display_name}: {status}")
        if result.errors:
            for err in result.errors:
                print(f"    {err}")
            all_ok = False

    return 0 if all_ok else 1


def cmd_migrate_config(args) -> int:
    config: dict = {"languages": {}}

    if args.r_config:
        with open(args.r_config) as f:
            r = yaml.safe_load(f)
        config["languages"]["r"] = {
            "enabled": True,
            "r_version": r.get("r_version", ""),
            "conda_packages": r.get("conda_packages", []),
            "cran_packages": r.get("cran_packages", []),
        }

    if args.scala_config:
        with open(args.scala_config) as f:
            sc = yaml.safe_load(f)
        config["languages"]["scala"] = {
            "enabled": True,
            "java_version": sc.get("java_version", "17"),
            "scala_version": sc.get("scala_version", "2.12"),
            "snowpark_version": sc.get("snowpark_version", "1.18.0"),
            "ammonite_version": sc.get("ammonite_version", "3.0.8"),
            "jvm_heap": sc.get("jvm_heap", "auto"),
            "jvm_options": sc.get("jvm_options", []),
            "extra_dependencies": sc.get("extra_dependencies", []),
            "spark_connect": sc.get("spark_connect", {}),
        }

    if args.julia_config:
        with open(args.julia_config) as f:
            jl = yaml.safe_load(f)
        config["languages"]["julia"] = {
            "enabled": True,
            "julia_version": jl.get("julia_version", ""),
            "julia_packages": jl.get("julia_packages", []),
            "depot_path": jl.get("depot_path", "auto"),
            "snowflake_odbc": jl.get("snowflake_odbc", {}),
            "sysimage": jl.get("sysimage", {}),
            "juliacall": jl.get("juliacall", {}),
        }

    with open(args.output, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"Unified config written to {args.output}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        commands = {
            "install": cmd_install,
            "generate-eai": cmd_generate_eai,
            "validate": cmd_validate,
            "migrate-config": cmd_migrate_config,
        }
        return commands[args.command](args)
    except ToolkitError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
