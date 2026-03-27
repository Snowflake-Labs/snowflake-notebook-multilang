"""Configuration loader and dataclass models.

Loads the unified YAML config, merges CLI overrides, and validates
the result into a typed ToolkitConfig tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .exceptions import ConfigError


# ---------------------------------------------------------------------------
# Dataclass models
# ---------------------------------------------------------------------------

@dataclass
class RConfig:
    enabled: bool = False
    r_version: str = ""
    conda_packages: List[str] = field(default_factory=lambda: [
        "r-tidyverse", "r-dbplyr", "r-httr2", "r-lazyeval", "r-reticulate>=1.25",
    ])
    cran_packages: List[str] = field(default_factory=list)
    pip_packages: List[str] = field(default_factory=list)
    addons: Dict[str, bool] = field(default_factory=lambda: {
        "adbc": False,
        "duckdb": False,
    })


@dataclass
class ScalaConfig:
    enabled: bool = False
    java_version: str = "17"
    scala_version: str = "2.12"
    snowpark_version: str = "1.18.0"
    ammonite_version: str = "3.0.8"
    jvm_heap: str = "auto"
    jvm_options: List[str] = field(default_factory=lambda: [
        "-Xms256m",
        "--add-opens=java.base/java.nio=ALL-UNNAMED",
    ])
    extra_dependencies: List[str] = field(default_factory=lambda: [
        "org.slf4j:slf4j-nop:1.7.36",
    ])
    spark_connect: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "pyspark_version": "3.5.6",
        "server_port": 15002,
    })


@dataclass
class JuliaConfig:
    enabled: bool = False
    julia_version: str = ""
    julia_packages: List[str] = field(default_factory=lambda: [
        "DataFrames", "CSV", "Arrow", "Statistics", "LinearAlgebra", "PythonCall",
    ])
    depot_path: str = "auto"
    snowflake_odbc: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "driver_version": "3.15.0",
    })
    sysimage: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "packages": ["DataFrames", "CSV", "Arrow"],
    })
    juliacall: Dict[str, Any] = field(default_factory=lambda: {
        "threads": "auto",
        "optimize": 2,
        "handle_signals": True,
        "startup_file": False,
    })


@dataclass
class MirrorsConfig:
    """Custom mirror URLs for air-gapped / Artifactory / Nexus environments.

    When set, these override the default public URLs for package downloads.
    Works with any artifact repository proxy (JFrog Artifactory, Sonatype
    Nexus, AWS CodeArtifact, etc.) -- the URLs just need to speak the
    standard protocol for each package manager.
    """
    conda_channel: str = ""
    pypi_index: str = ""
    cran_mirror: str = ""
    micromamba_url: str = ""
    ssl_cert_path: str = ""


@dataclass
class NetworkRuleConfig:
    apply_in_installer: bool = True
    account: str = ""
    rule_name: str = "multilang_notebook_egress"
    integration_name: str = "multilang_notebook_eai"
    grant_to_role: str = ""
    sql_export_path: str = "./eai_setup.sql"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_file: str = ""
    json_format: bool = False


@dataclass
class ToolkitConfig:
    env_name: str = "workspace_env"
    micromamba_root: str = "~/micromamba"
    force_reinstall: bool = False
    languages: Dict[str, Any] = field(default_factory=dict)
    r: RConfig = field(default_factory=RConfig)
    scala: ScalaConfig = field(default_factory=ScalaConfig)
    julia: JuliaConfig = field(default_factory=JuliaConfig)
    mirrors: MirrorsConfig = field(default_factory=MirrorsConfig)
    network_rule: NetworkRuleConfig = field(default_factory=NetworkRuleConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @property
    def micromamba_root_expanded(self) -> str:
        return os.path.expanduser(self.micromamba_root)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def _merge_r(raw: dict) -> RConfig:
    """Build RConfig from the 'r' or 'languages.r' section of raw YAML."""
    cfg = RConfig()
    if not raw:
        return cfg
    cfg.enabled = bool(raw.get("enabled", False))
    cfg.r_version = str(raw.get("r_version", "") or "")
    if "conda_packages" in raw and raw["conda_packages"] is not None:
        cfg.conda_packages = list(raw["conda_packages"])
    if "cran_packages" in raw and raw["cran_packages"] is not None:
        cfg.cran_packages = list(raw["cran_packages"])
    if "pip_packages" in raw and raw["pip_packages"] is not None:
        cfg.pip_packages = list(raw["pip_packages"])
    addons = raw.get("addons", raw)  # support both nested and flat
    cfg.addons = {
        "adbc": bool(addons.get("adbc", addons.get("install_adbc", False))),
        "duckdb": bool(addons.get("duckdb", addons.get("install_duckdb", False))),
    }
    return cfg


def _merge_scala(raw: dict) -> ScalaConfig:
    cfg = ScalaConfig()
    if not raw:
        return cfg
    cfg.enabled = bool(raw.get("enabled", False))
    for attr in ("java_version", "scala_version", "snowpark_version",
                 "ammonite_version", "jvm_heap"):
        if attr in raw:
            setattr(cfg, attr, str(raw[attr]))
    if "jvm_options" in raw and raw["jvm_options"] is not None:
        cfg.jvm_options = list(raw["jvm_options"])
    if "extra_dependencies" in raw and raw["extra_dependencies"] is not None:
        cfg.extra_dependencies = list(raw["extra_dependencies"])
    if "spark_connect" in raw and raw["spark_connect"] is not None:
        sc = raw["spark_connect"]
        cfg.spark_connect = {
            "enabled": bool(sc.get("enabled", False)),
            "pyspark_version": str(sc.get("pyspark_version", "3.5.6")),
            "server_port": int(sc.get("server_port", 15002)),
        }
    return cfg


def _merge_julia(raw: dict) -> JuliaConfig:
    cfg = JuliaConfig()
    if not raw:
        return cfg
    cfg.enabled = bool(raw.get("enabled", False))
    cfg.julia_version = str(raw.get("julia_version", "") or "")
    if "julia_packages" in raw and raw["julia_packages"] is not None:
        cfg.julia_packages = list(raw["julia_packages"])
    cfg.depot_path = str(raw.get("depot_path", "auto"))
    if "snowflake_odbc" in raw and raw["snowflake_odbc"] is not None:
        odbc = raw["snowflake_odbc"]
        cfg.snowflake_odbc = {
            "enabled": bool(odbc.get("enabled", False)),
            "driver_version": str(odbc.get("driver_version", "3.15.0")),
        }
    if "sysimage" in raw and raw["sysimage"] is not None:
        si = raw["sysimage"]
        cfg.sysimage = {
            "enabled": bool(si.get("enabled", False)),
            "packages": list(si.get("packages", ["DataFrames", "CSV", "Arrow"])),
        }
    if "juliacall" in raw and raw["juliacall"] is not None:
        cfg.juliacall = dict(raw["juliacall"])
    return cfg


def load_config(path: str) -> ToolkitConfig:
    """Load a YAML config file and return a validated ToolkitConfig."""
    if not os.path.isfile(path):
        raise ConfigError(f"Configuration file not found: {path}")

    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    return _build_config(raw)


def _build_config(raw: dict) -> ToolkitConfig:
    """Build ToolkitConfig from a raw dict (parsed YAML or programmatic)."""
    cfg = ToolkitConfig()
    cfg.env_name = str(raw.get("env_name", cfg.env_name))
    cfg.micromamba_root = str(raw.get("micromamba_root", cfg.micromamba_root))
    cfg.force_reinstall = bool(raw.get("force_reinstall", False))

    # Languages can be nested under "languages:" or at top level
    langs = raw.get("languages", {}) or {}

    # Support both unified format (languages.r.enabled) and legacy (languages.r: true)
    r_raw = langs.get("r", raw.get("r", {}))
    if isinstance(r_raw, bool):
        r_raw = {"enabled": r_raw}
    elif r_raw is None:
        r_raw = {}
    cfg.r = _merge_r(r_raw)

    scala_raw = langs.get("scala", raw.get("scala", {}))
    if isinstance(scala_raw, bool):
        scala_raw = {"enabled": scala_raw}
    elif scala_raw is None:
        scala_raw = {}
    cfg.scala = _merge_scala(scala_raw)

    julia_raw = langs.get("julia", raw.get("julia", {}))
    if isinstance(julia_raw, bool):
        julia_raw = {"enabled": julia_raw}
    elif julia_raw is None:
        julia_raw = {}
    cfg.julia = _merge_julia(julia_raw)

    # Mirrors config (Artifactory / Nexus / air-gapped environments)
    mir_raw = raw.get("mirrors", {}) or {}
    cfg.mirrors = MirrorsConfig(
        conda_channel=str(mir_raw.get("conda_channel", "")),
        pypi_index=str(mir_raw.get("pypi_index", "")),
        cran_mirror=str(mir_raw.get("cran_mirror", "")),
        micromamba_url=str(mir_raw.get("micromamba_url", "")),
        ssl_cert_path=str(mir_raw.get("ssl_cert_path", "")),
    )

    # Network rule config
    nr_raw = raw.get("network_rule", {}) or {}
    cfg.network_rule = NetworkRuleConfig(
        apply_in_installer=bool(nr_raw.get("apply_in_installer", True)),
        account=str(nr_raw.get("account", "")),
        rule_name=str(nr_raw.get("rule_name", "multilang_notebook_egress")),
        integration_name=str(nr_raw.get("integration_name", "multilang_notebook_eai")),
        grant_to_role=str(nr_raw.get("grant_to_role", "")),
        sql_export_path=str(nr_raw.get("sql_export_path", "./eai_setup.sql")),
    )

    # Logging config
    log_raw = raw.get("logging", {}) or {}
    cfg.logging = LoggingConfig(
        level=str(log_raw.get("level", "INFO")),
        log_file=str(log_raw.get("log_file", "")),
        json_format=bool(log_raw.get("json_format", False)),
    )

    return cfg


def config_to_dict(cfg: ToolkitConfig) -> dict:
    """Serialize a ToolkitConfig back to a plain dict for YAML output."""
    return {
        "env_name": cfg.env_name,
        "micromamba_root": cfg.micromamba_root,
        "force_reinstall": cfg.force_reinstall,
        "languages": {
            "r": {
                "enabled": cfg.r.enabled,
                "r_version": cfg.r.r_version,
                "conda_packages": cfg.r.conda_packages,
                "cran_packages": cfg.r.cran_packages,
                "pip_packages": cfg.r.pip_packages,
                "addons": cfg.r.addons,
            },
            "scala": {
                "enabled": cfg.scala.enabled,
                "java_version": cfg.scala.java_version,
                "scala_version": cfg.scala.scala_version,
                "snowpark_version": cfg.scala.snowpark_version,
                "ammonite_version": cfg.scala.ammonite_version,
                "jvm_heap": cfg.scala.jvm_heap,
                "jvm_options": cfg.scala.jvm_options,
                "extra_dependencies": cfg.scala.extra_dependencies,
                "spark_connect": cfg.scala.spark_connect,
            },
            "julia": {
                "enabled": cfg.julia.enabled,
                "julia_version": cfg.julia.julia_version,
                "julia_packages": cfg.julia.julia_packages,
                "depot_path": cfg.julia.depot_path,
                "snowflake_odbc": cfg.julia.snowflake_odbc,
                "sysimage": cfg.julia.sysimage,
                "juliacall": cfg.julia.juliacall,
            },
        },
        "mirrors": {
            "conda_channel": cfg.mirrors.conda_channel,
            "pypi_index": cfg.mirrors.pypi_index,
            "cran_mirror": cfg.mirrors.cran_mirror,
            "micromamba_url": cfg.mirrors.micromamba_url,
            "ssl_cert_path": cfg.mirrors.ssl_cert_path,
        },
        "network_rule": {
            "apply_in_installer": cfg.network_rule.apply_in_installer,
            "account": cfg.network_rule.account,
            "rule_name": cfg.network_rule.rule_name,
            "integration_name": cfg.network_rule.integration_name,
            "grant_to_role": cfg.network_rule.grant_to_role,
            "sql_export_path": cfg.network_rule.sql_export_path,
        },
        "logging": {
            "level": cfg.logging.level,
            "log_file": cfg.logging.log_file,
            "json_format": cfg.logging.json_format,
        },
    }


def apply_cli_overrides(cfg: ToolkitConfig, **kwargs: Any) -> ToolkitConfig:
    """Apply CLI flag overrides to an existing config.

    Supported kwargs:
        languages: list[str] -- enable these languages (overrides YAML)
        r_adbc: bool -- enable R ADBC addon
        r_duckdb: bool -- enable R DuckDB addon
        scala_spark_connect: bool -- enable Scala Spark Connect
        verbose: bool -- set log level to DEBUG
        force: bool -- force reinstall
        apply_eai: bool -- try to apply EAI in installer
        account: str -- Snowflake account for EAI
    """
    languages = kwargs.get("languages")
    if languages:
        cfg.r.enabled = "r" in languages
        cfg.scala.enabled = "scala" in languages
        cfg.julia.enabled = "julia" in languages

    if kwargs.get("r_adbc"):
        cfg.r.addons["adbc"] = True
        cfg.r.enabled = True
    if kwargs.get("r_duckdb"):
        cfg.r.addons["duckdb"] = True
        cfg.r.enabled = True
    if kwargs.get("scala_spark_connect"):
        cfg.scala.spark_connect["enabled"] = True
        cfg.scala.enabled = True
    if kwargs.get("verbose"):
        cfg.logging.level = "DEBUG"
    if kwargs.get("force"):
        cfg.force_reinstall = True
    if "apply_eai" in kwargs:
        cfg.network_rule.apply_in_installer = bool(kwargs["apply_eai"])
    if kwargs.get("account"):
        cfg.network_rule.account = kwargs["account"]

    return cfg
