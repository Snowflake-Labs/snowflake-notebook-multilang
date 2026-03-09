"""Tests for network rule SQL generation."""

from __future__ import annotations

import os
import tempfile

import pytest

from sfnb_multilang.config import ToolkitConfig, apply_cli_overrides
from sfnb_multilang.network_rules import (
    SHARED_HOSTS,
    export_eai_sql,
    generate_network_rule_sql,
)


class TestGenerateSQL:
    """Test generate_network_rule_sql()."""

    def test_shared_hosts_always_present(self):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        sql = generate_network_rule_sql(cfg)
        for h in SHARED_HOSTS:
            assert h["host"] in sql

    def test_r_hosts(self):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        cfg.r.cran_packages = ["ggplot2"]
        sql = generate_network_rule_sql(cfg)
        assert "cloud.r-project.org" in sql

    def test_r_adbc_hosts(self):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        cfg.r.addons["adbc"] = True
        sql = generate_network_rule_sql(cfg)
        assert "community.r-multiverse.org" in sql
        assert "proxy.golang.org" in sql

    def test_scala_hosts(self):
        cfg = ToolkitConfig()
        cfg.scala.enabled = True
        sql = generate_network_rule_sql(cfg)
        assert "repo1.maven.org" in sql
        assert "github.com" in sql

    def test_julia_hosts(self):
        cfg = ToolkitConfig()
        cfg.julia.enabled = True
        sql = generate_network_rule_sql(cfg)
        assert "github.com" in sql
        assert "pypi.org" in sql
        # Julia package server bypass: should NOT include pkg.julialang.org
        assert "pkg.julialang.org" not in sql

    def test_account_host(self):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        sql = generate_network_rule_sql(cfg, account="myaccount")
        assert "myaccount.snowflakecomputing.com" in sql

    def test_custom_names(self):
        cfg = ToolkitConfig()
        cfg.scala.enabled = True
        sql = generate_network_rule_sql(
            cfg, rule_name="my_rule", integration_name="my_eai",
        )
        assert "CREATE OR REPLACE NETWORK RULE my_rule" in sql
        assert "CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION my_eai" in sql
        assert "ALLOWED_NETWORK_RULES = (my_rule)" in sql

    def test_deduplication(self):
        """GitHub host needed by both Scala and Julia should appear once."""
        cfg = ToolkitConfig()
        cfg.scala.enabled = True
        cfg.julia.enabled = True
        sql = generate_network_rule_sql(cfg)
        # github.com should only appear once in the VALUE_LIST
        value_section = sql.split("VALUE_LIST")[1].split(";")[0]
        assert value_section.count("github.com") == 1

    def test_no_languages(self):
        cfg = ToolkitConfig()
        sql = generate_network_rule_sql(cfg)
        # Should still have shared hosts
        assert "micro.mamba.pm" in sql

    def test_all_languages(self):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        cfg.r.cran_packages = ["dplyr"]
        cfg.r.addons["adbc"] = True
        cfg.scala.enabled = True
        cfg.julia.enabled = True
        sql = generate_network_rule_sql(cfg)
        # Should include hosts from all languages
        assert "cloud.r-project.org" in sql
        assert "repo1.maven.org" in sql
        assert "github.com" in sql


class TestExportSQL:
    """Test export_eai_sql() writes to file."""

    def test_writes_file(self, tmp_path):
        cfg = ToolkitConfig()
        cfg.r.enabled = True
        out = os.path.join(str(tmp_path), "test_eai.sql")
        result = export_eai_sql(cfg, output_path=out)
        assert os.path.isfile(result)
        with open(result) as f:
            content = f.read()
        assert "CREATE OR REPLACE NETWORK RULE" in content
