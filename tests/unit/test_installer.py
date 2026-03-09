"""Tests for the Installer, version conflict resolution, and package collection."""

from __future__ import annotations

import pytest

from sfnb_multilang.config import ToolkitConfig
from sfnb_multilang.exceptions import PackageConflictError
from sfnb_multilang.languages.base import (
    PackageRequest,
    parse_package,
    resolve_version_conflict,
)


# ---------------------------------------------------------------------------
# parse_package tests
# ---------------------------------------------------------------------------

class TestParsePackage:

    def test_plain_name(self):
        r = parse_package("r-base", "r")
        assert r.base_name == "r-base"
        assert r.constraint == ""
        assert r.requesting_plugin == "r"

    def test_pinned_version(self):
        r = parse_package("openjdk=17", "scala")
        assert r.base_name == "openjdk"
        assert r.constraint == "=17"
        assert r.raw == "openjdk=17"

    def test_range_version(self):
        r = parse_package("r-reticulate>=1.25", "r")
        assert r.base_name == "r-reticulate"
        assert r.constraint == ">=1.25"

    def test_double_equals(self):
        r = parse_package("pyspark==3.5.6", "scala")
        assert r.base_name == "pyspark"
        assert r.constraint == "==3.5.6"


# ---------------------------------------------------------------------------
# resolve_version_conflict tests
# ---------------------------------------------------------------------------

class TestResolveVersionConflict:

    def test_rule1_identical(self):
        """Identical requests should deduplicate silently."""
        result = resolve_version_conflict("openjdk", [
            PackageRequest("openjdk=17", "openjdk", "=17", "scala"),
            PackageRequest("openjdk=17", "openjdk", "=17", "kotlin"),
        ])
        assert result == "openjdk=17"

    def test_rule2_pinned_vs_unpinned(self):
        """Pinned + unpinned -> use pinned version."""
        result = resolve_version_conflict("openjdk", [
            PackageRequest("openjdk=17", "openjdk", "=17", "scala"),
            PackageRequest("openjdk", "openjdk", "", "kotlin"),
        ])
        assert result == "openjdk=17"

    def test_rule3_range_tightest(self):
        """Multiple ranges -> pick the tightest."""
        result = resolve_version_conflict("r-reticulate", [
            PackageRequest("r-reticulate>=1.20", "r-reticulate", ">=1.20", "r"),
            PackageRequest("r-reticulate>=1.25", "r-reticulate", ">=1.25", "julia"),
        ])
        assert result == "r-reticulate>=1.25"

    def test_rule4_conflicting_pins(self):
        """Two different pin versions -> raise PackageConflictError."""
        with pytest.raises(PackageConflictError, match="openjdk"):
            resolve_version_conflict("openjdk", [
                PackageRequest("openjdk=17", "openjdk", "=17", "scala"),
                PackageRequest("openjdk=11", "openjdk", "=11", "kotlin"),
            ])

    def test_rule5_range_vs_incompatible_pin(self):
        """Pin below range minimum -> raise PackageConflictError."""
        with pytest.raises(PackageConflictError, match="openjdk"):
            resolve_version_conflict("openjdk", [
                PackageRequest("openjdk=11", "openjdk", "=11", "scala"),
                PackageRequest("openjdk>=17", "openjdk", ">=17", "kotlin"),
            ])

    def test_pin_satisfies_range(self):
        """Pin >= range minimum -> use pinned version."""
        result = resolve_version_conflict("openjdk", [
            PackageRequest("openjdk=17", "openjdk", "=17", "scala"),
            PackageRequest("openjdk>=11", "openjdk", ">=11", "kotlin"),
        ])
        assert result == "openjdk=17"

    def test_single_request_passthrough(self):
        """Single request should pass through unchanged."""
        result = resolve_version_conflict("r-base", [
            PackageRequest("r-base=4.3.2", "r-base", "=4.3.2", "r"),
        ])
        assert result == "r-base=4.3.2"

    def test_all_unpinned(self):
        """Multiple unpinned -> first wins."""
        result = resolve_version_conflict("julia", [
            PackageRequest("julia", "julia", "", "julia"),
            PackageRequest("julia", "julia", "", "r"),
        ])
        assert result == "julia"


# ---------------------------------------------------------------------------
# Installer package collection (unit-level)
# ---------------------------------------------------------------------------

class TestInstallerPackageCollection:
    """Test that the Installer correctly collects and deduplicates packages."""

    def test_r_only_packages(self):
        from sfnb_multilang.installer import Installer

        cfg = ToolkitConfig()
        cfg.r.enabled = True
        installer = Installer(cfg)

        pkgs = installer._collect_conda_packages()
        base_names = [p.split("=")[0].split(">")[0] for p in pkgs]
        assert "r-base" in base_names
        assert "r-tidyverse" in base_names

    def test_scala_only_packages(self):
        from sfnb_multilang.installer import Installer

        cfg = ToolkitConfig()
        cfg.scala.enabled = True
        installer = Installer(cfg)

        pkgs = installer._collect_conda_packages()
        base_names = [p.split("=")[0].split(">")[0] for p in pkgs]
        assert "openjdk" in base_names

        pip_pkgs = installer._collect_pip_packages()
        pip_names = [p.split("=")[0].split(">")[0] for p in pip_pkgs]
        assert "JPype1" in pip_names

    def test_julia_only_packages(self):
        from sfnb_multilang.installer import Installer

        cfg = ToolkitConfig()
        cfg.julia.enabled = True
        installer = Installer(cfg)

        pkgs = installer._collect_conda_packages()
        base_names = [p.split("=")[0].split(">")[0] for p in pkgs]
        assert "julia" in base_names

        pip_pkgs = installer._collect_pip_packages()
        pip_names = [p.split("=")[0].split(">")[0] for p in pip_pkgs]
        assert "juliacall" in pip_names

    def test_all_languages_no_conflict(self):
        """All three languages enabled should produce no conflicts."""
        from sfnb_multilang.installer import Installer

        cfg = ToolkitConfig()
        cfg.r.enabled = True
        cfg.scala.enabled = True
        cfg.julia.enabled = True
        installer = Installer(cfg)

        pkgs = installer._collect_conda_packages()
        base_names = [p.split("=")[0].split(">")[0] for p in pkgs]
        assert "r-base" in base_names
        assert "openjdk" in base_names
        assert "julia" in base_names
