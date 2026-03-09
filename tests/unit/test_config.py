"""Tests for YAML config loading, CLI overrides, and serialization."""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml

from sfnb_multilang.config import (
    ToolkitConfig,
    apply_cli_overrides,
    config_to_dict,
    load_config,
)
from sfnb_multilang.exceptions import ConfigError


class TestLoadConfig:
    """Test YAML -> ToolkitConfig."""

    def _write_yaml(self, data: dict, tmpdir: str) -> str:
        path = os.path.join(tmpdir, "cfg.yaml")
        with open(path, "w") as f:
            yaml.dump(data, f)
        return path

    def test_minimal_config(self, tmp_path):
        path = self._write_yaml(
            {"languages": {"r": {"enabled": True}}},
            str(tmp_path),
        )
        cfg = load_config(path)
        assert cfg.r.enabled is True
        assert cfg.scala.enabled is False
        assert cfg.julia.enabled is False
        assert cfg.env_name == "workspace_env"

    def test_file_not_found(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/path.yaml")

    def test_full_config(self, tmp_path):
        data = {
            "env_name": "my_env",
            "micromamba_root": "/opt/mm",
            "force_reinstall": True,
            "languages": {
                "r": {
                    "enabled": True,
                    "r_version": "4.3.2",
                    "conda_packages": ["r-base", "r-dplyr"],
                    "cran_packages": ["ggplot2"],
                    "addons": {"adbc": True, "duckdb": False},
                },
                "scala": {
                    "enabled": True,
                    "java_version": "17",
                    "scala_version": "2.13",
                    "snowpark_version": "1.20.0",
                },
                "julia": {"enabled": False},
            },
            "network_rule": {
                "apply_in_installer": False,
                "rule_name": "custom_rule",
            },
            "logging": {"level": "DEBUG", "json_format": True},
        }
        path = self._write_yaml(data, str(tmp_path))
        cfg = load_config(path)

        assert cfg.env_name == "my_env"
        assert cfg.force_reinstall is True
        assert cfg.r.r_version == "4.3.2"
        assert cfg.r.addons["adbc"] is True
        assert cfg.scala.scala_version == "2.13"
        assert cfg.scala.snowpark_version == "1.20.0"
        assert cfg.julia.enabled is False
        assert cfg.network_rule.apply_in_installer is False
        assert cfg.network_rule.rule_name == "custom_rule"
        assert cfg.logging.level == "DEBUG"

    def test_boolean_language_shorthand(self, tmp_path):
        """Support 'languages.r: true' as a shorthand."""
        data = {"languages": {"r": True, "scala": False}}
        path = self._write_yaml(data, str(tmp_path))
        cfg = load_config(path)
        assert cfg.r.enabled is True
        assert cfg.scala.enabled is False

    def test_empty_file(self, tmp_path):
        path = os.path.join(str(tmp_path), "empty.yaml")
        with open(path, "w") as f:
            f.write("")
        cfg = load_config(path)
        assert cfg.r.enabled is False
        assert cfg.scala.enabled is False


class TestCliOverrides:
    """Test apply_cli_overrides()."""

    def test_enable_languages(self):
        cfg = ToolkitConfig()
        cfg = apply_cli_overrides(cfg, languages=["r", "julia"])
        assert cfg.r.enabled is True
        assert cfg.scala.enabled is False
        assert cfg.julia.enabled is True

    def test_verbose(self):
        cfg = ToolkitConfig()
        cfg = apply_cli_overrides(cfg, verbose=True)
        assert cfg.logging.level == "DEBUG"

    def test_force(self):
        cfg = ToolkitConfig()
        cfg = apply_cli_overrides(cfg, force=True)
        assert cfg.force_reinstall is True

    def test_r_addons(self):
        cfg = ToolkitConfig()
        cfg = apply_cli_overrides(cfg, r_adbc=True, r_duckdb=True)
        assert cfg.r.enabled is True
        assert cfg.r.addons["adbc"] is True
        assert cfg.r.addons["duckdb"] is True

    def test_no_eai(self):
        cfg = ToolkitConfig()
        cfg = apply_cli_overrides(cfg, apply_eai=False)
        assert cfg.network_rule.apply_in_installer is False


class TestConfigRoundTrip:
    """Test config_to_dict() -> yaml -> load_config()."""

    def test_round_trip(self, tmp_path):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        cfg.scala.enabled = True
        cfg.julia.enabled = False

        d = config_to_dict(cfg)
        path = os.path.join(str(tmp_path), "rt.yaml")
        with open(path, "w") as f:
            yaml.dump(d, f)

        cfg2 = load_config(path)
        assert cfg2.r.enabled is True
        assert cfg2.scala.enabled is True
        assert cfg2.julia.enabled is False
