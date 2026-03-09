"""Julia language plugin -- ported from setup_julia_environment.sh.

Includes the JULIA_PKG_SERVER="" workaround for SPCS DNS issues with the
Julia package server (pkg.julialang.org -> storage.julialang.net chain).
With this bypass, all Julia packages are cloned via Git from github.com.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from typing import Any

from ..config import ToolkitConfig
from ..exceptions import PackageInstallError, PluginError
from ..shared.download import retry
from ..shared.subprocess_utils import run_cmd
from .base import LanguagePlugin, PluginResult

logger = logging.getLogger("sfnb_multilang.languages.julia")


class JuliaPlugin(LanguagePlugin):

    @property
    def name(self) -> str:
        return "julia"

    @property
    def display_name(self) -> str:
        return "Julia"

    # -----------------------------------------------------------------
    # Package declarations
    # -----------------------------------------------------------------

    def get_conda_packages(self, config: ToolkitConfig) -> list[str]:
        jl = config.julia
        return [f"julia={jl.julia_version}" if jl.julia_version else "julia"]

    def get_pip_packages(self, config: ToolkitConfig) -> list[str]:
        return ["juliacall"]

    def get_network_hosts(self, config: ToolkitConfig) -> list[dict]:
        jl = config.julia
        # With JULIA_PKG_SERVER="" we bypass the package server chain
        # and clone everything via Git from github.com
        hosts: list[dict] = [
            {"host": "github.com", "port": 443,
             "purpose": "Julia registry + package source (Git clone fallback)",
             "required": True},
            {"host": "pypi.org", "port": 443,
             "purpose": "pip (juliacall)", "required": True},
            {"host": "files.pythonhosted.org", "port": 443,
             "purpose": "pip files (juliacall)", "required": True},
        ]

        if jl.snowflake_odbc.get("enabled"):
            hosts.append({
                "host": "sfc-repo.snowflakecomputing.com", "port": 443,
                "purpose": "Snowflake ODBC driver", "required": True,
            })

        return hosts

    # -----------------------------------------------------------------
    # Post-install
    # -----------------------------------------------------------------

    def post_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        jl = config.julia
        warnings: list[str] = []

        # Add julia to PATH
        os.environ["PATH"] = (
            os.path.join(env_prefix, "bin") + os.pathsep + os.environ.get("PATH", "")
        )

        # Bypass Julia package server -- use Git clone from github.com
        # This avoids SPCS DNS issues with pkg.julialang.org redirect chain
        os.environ["JULIA_PKG_SERVER"] = ""

        # Configure depot path
        depot = self._resolve_depot(jl)
        os.environ["JULIA_DEPOT_PATH"] = depot

        # Verify Julia
        try:
            result = run_cmd(["julia", "--version"], description="Verify Julia")
            logger.info("  Julia: %s", result.stdout.strip())
        except Exception:
            raise PluginError("julia", "post_install", "Julia not found after installation")

        # Install Julia packages
        self._install_julia_packages(env_prefix, jl)

        # ODBC (optional)
        if jl.snowflake_odbc.get("enabled"):
            self._install_odbc(env_prefix, jl)

        # Sysimage (optional)
        if jl.sysimage.get("enabled"):
            self._build_sysimage(env_prefix, jl)

        version = self._get_julia_version(env_prefix)

        # Write metadata for julia_helpers.py
        self._write_metadata(env_prefix, depot, jl, version)

        return PluginResult(
            success=True, language="julia",
            version=version, env_prefix=env_prefix,
            warnings=warnings,
        )

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        errors: list[str] = []

        julia_bin = os.path.join(env_prefix, "bin", "julia")
        if not os.path.isfile(julia_bin):
            errors.append(f"Julia binary not found at {julia_bin}")

        if not errors:
            try:
                result = run_cmd(
                    [julia_bin, "-e", 'using DataFrames; println("DataFrames OK")'],
                    description="Validate Julia",
                    check=False,
                )
                if result.returncode != 0:
                    errors.append(f"Julia package check failed: {result.stderr}")
                else:
                    logger.info("  Julia validation: %s", result.stdout.strip())
            except Exception as exc:
                errors.append(f"Julia execution failed: {exc}")

        return PluginResult(
            success=len(errors) == 0, language="julia",
            version=self._get_julia_version(env_prefix),
            env_prefix=env_prefix, errors=errors,
        )

    # -----------------------------------------------------------------
    # Internal methods
    # -----------------------------------------------------------------

    def _resolve_depot(self, jl_cfg: Any) -> str:
        """Determine and create the Julia depot path."""
        depot = jl_cfg.depot_path
        if depot == "auto":
            persistent = os.environ.get("PERSISTENT_DIR")
            if persistent:
                depot = os.path.join(persistent, "julia_depot")
            else:
                depot = os.path.expanduser("~/julia_depot")
        else:
            depot = os.path.expanduser(depot)

        os.makedirs(depot, exist_ok=True)
        logger.info("  Julia depot: %s", depot)
        return depot

    @retry(max_attempts=2, delay=10)
    def _install_julia_packages(self, env_prefix: str, jl_cfg: Any) -> None:
        """Install Julia packages via Pkg.add()."""
        packages = jl_cfg.julia_packages
        if not packages:
            logger.info("  No Julia packages to install")
            return

        logger.info("  Installing Julia packages: %s", ", ".join(packages))
        julia_bin = os.path.join(env_prefix, "bin", "julia")

        pkg_list = ", ".join(f'"{p}"' for p in packages)
        julia_code = textwrap.dedent(f"""\
            import Pkg
            Pkg.add([{pkg_list}])
            println("Julia package installation complete")
        """)

        env = dict(os.environ)
        env["JULIA_PKG_SERVER"] = ""

        run_cmd(
            [julia_bin, "-e", julia_code],
            description="Install Julia packages",
            env=env,
        )
        logger.info("    Julia packages installed")

    @retry(max_attempts=2, delay=5)
    def _install_odbc(self, env_prefix: str, jl_cfg: Any) -> None:
        """Download and configure the Snowflake ODBC driver."""
        logger.info("  Installing Snowflake ODBC driver...")
        driver_ver = jl_cfg.snowflake_odbc.get("driver_version", "3.15.0")
        driver_dir = os.path.expanduser("~/snowflake_odbc")
        os.makedirs(driver_dir, exist_ok=True)

        tarball = f"snowflake_linux_x8664_odbc-{driver_ver}.tgz"
        url = f"https://sfc-repo.snowflakecomputing.com/odbc/linux/{driver_ver}/{tarball}"

        driver_lib = os.path.join(driver_dir, "lib", "libSnowflake.so")
        if os.path.isfile(driver_lib):
            logger.info("    ODBC driver already installed (skipping)")
            return

        run_cmd(
            ["curl", "-fL", "-o", os.path.join(driver_dir, tarball), url],
            description="Download ODBC driver",
        )
        run_cmd(
            ["tar", "-xzf", os.path.join(driver_dir, tarball), "-C", driver_dir,
             "--strip-components=1"],
            description="Extract ODBC driver",
        )

        # Write odbcinst.ini
        ini_path = os.path.join(driver_dir, "odbcinst.ini")
        with open(ini_path, "w") as f:
            f.write(f"[Snowflake]\nDriver={driver_lib}\n")

        os.environ["ODBCSYSINI"] = driver_dir
        logger.info("    ODBC driver installed at %s", driver_dir)

    def _build_sysimage(self, env_prefix: str, jl_cfg: Any) -> None:
        """Build a custom Julia sysimage with PackageCompiler.jl."""
        logger.info("  Building Julia sysimage (this may take 5-15 minutes)...")
        julia_bin = os.path.join(env_prefix, "bin", "julia")
        packages = jl_cfg.sysimage.get("packages", [])
        if not packages:
            logger.warning("    No packages specified for sysimage, skipping")
            return

        depot = os.environ.get("JULIA_DEPOT_PATH", os.path.expanduser("~/julia_depot"))
        sysimage_path = os.path.join(depot, "sysimage.so")

        if os.path.isfile(sysimage_path):
            logger.info("    Sysimage already exists at %s (skipping)", sysimage_path)
            return

        using_stmts = "; ".join(f"using {p}" for p in packages)
        pkg_syms = ", ".join(f":{p}" for p in packages)
        julia_code = textwrap.dedent(f"""\
            import Pkg
            Pkg.add("PackageCompiler")
            using PackageCompiler
            create_sysimage(
                [{pkg_syms}];
                sysimage_path="{sysimage_path}",
                precompile_statements_file=nothing,
            )
            println("Sysimage built at {sysimage_path}")
        """)

        env = dict(os.environ)
        env["JULIA_PKG_SERVER"] = ""

        run_cmd(
            [julia_bin, "-e", julia_code],
            description="Build Julia sysimage",
            env=env,
            timeout=1800,
        )
        logger.info("    Sysimage built at %s", sysimage_path)

    def _write_metadata(
        self, env_prefix: str, depot: str, jl_cfg: Any, version: str
    ) -> None:
        """Write julia_env_metadata.json for julia_helpers.py."""
        import re
        julia_bin = os.path.join(env_prefix, "bin", "julia")
        m = re.search(r"(\d+)\.(\d+)", version)
        if m:
            project = os.path.join(
                depot, "environments",
                f"v{m.group(1)}.{m.group(2)}"
            )
        else:
            project = depot

        meta = {
            "env_prefix": env_prefix,
            "julia_bin": julia_bin,
            "julia_depot_path": depot,
            "julia_project": project,
            "julia_version": version,
            "juliacall_threads": "auto",
            "juliacall_optimize": 2,
            "sysimage_path": None,
            "julia_packages": jl_cfg.julia_packages,
        }

        sysimage = os.path.join(depot, "sysimage.so")
        if os.path.isfile(sysimage):
            meta["sysimage_path"] = sysimage

        meta_path = os.path.join(depot, "julia_env_metadata.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        logger.info("  Metadata written to %s", meta_path)

    def _get_julia_version(self, env_prefix: str) -> str:
        julia_bin = os.path.join(env_prefix, "bin", "julia")
        if not os.path.isfile(julia_bin):
            return "unknown"
        try:
            result = run_cmd(
                [julia_bin, "--version"],
                description="Get Julia version",
                check=False,
            )
            return (result.stdout or "").strip()
        except Exception:
            return "unknown"
