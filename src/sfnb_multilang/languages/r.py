"""R language plugin -- ported from setup_r_environment.sh."""

from __future__ import annotations

import logging
import os
import textwrap
from typing import Any

from ..config import ToolkitConfig
from ..exceptions import PackageInstallError, PluginError
from ..shared.conda_env import get_missing_packages
from ..shared.download import retry
from ..shared.subprocess_utils import run_cmd
from .base import LanguagePlugin, PluginResult

logger = logging.getLogger("sfnb_multilang.languages.r")


class RPlugin(LanguagePlugin):

    @property
    def name(self) -> str:
        return "r"

    @property
    def display_name(self) -> str:
        return "R"

    # -----------------------------------------------------------------
    # Package declarations
    # -----------------------------------------------------------------

    def get_conda_packages(self, config: ToolkitConfig) -> list[str]:
        r_cfg = config.r
        packages = list(r_cfg.conda_packages)

        has_r_base = any(p.startswith("r-base") for p in packages)
        if not has_r_base:
            entry = f"r-base={r_cfg.r_version}" if r_cfg.r_version else "r-base"
            packages.insert(0, entry)

        if not r_cfg.r_version:
            logger.warning(
                "r_version is not pinned in config. An unconstrained R "
                "install pulls the latest conda-forge release, which may "
                "not yet have all R packages rebuilt. Pin r_version to a "
                "known-good release (e.g. r_version: '4.5.2') in your "
                "YAML config to avoid package compatibility issues."
            )

        return packages

    def get_pip_packages(self, config: ToolkitConfig) -> list[str]:
        return []

    def get_network_hosts(self, config: ToolkitConfig) -> list[dict]:
        r_cfg = config.r
        hosts: list[dict] = []

        if r_cfg.cran_packages:
            hosts.append({
                "host": "cloud.r-project.org", "port": 443,
                "purpose": "CRAN package downloads", "required": True,
            })

        if r_cfg.addons.get("adbc"):
            hosts.extend([
                {"host": "community.r-multiverse.org", "port": 443,
                 "purpose": "ADBC R packages (index)", "required": True},
                {"host": "cdn.r-universe.dev", "port": 443,
                 "purpose": "ADBC R packages (CDN)", "required": True},
                {"host": "proxy.golang.org", "port": 443,
                 "purpose": "Go modules for ADBC build", "required": True},
                {"host": "storage.googleapis.com", "port": 443,
                 "purpose": "Go module downloads", "required": True},
                {"host": "sum.golang.org", "port": 443,
                 "purpose": "Go checksum verification", "required": True},
            ])

        if r_cfg.addons.get("duckdb"):
            hosts.extend([
                {"host": "community-extensions.duckdb.org", "port": 443,
                 "purpose": "DuckDB community extensions", "required": True},
                {"host": "extensions.duckdb.org", "port": 443,
                 "purpose": "DuckDB core extensions", "required": True},
            ])

        return hosts

    # -----------------------------------------------------------------
    # Post-install
    # -----------------------------------------------------------------

    def post_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        r_cfg = config.r
        warnings: list[str] = []

        # Fix library symlinks (libz.so, liblzma.so)
        self._fix_symlinks(env_prefix)

        # Install CRAN packages
        if r_cfg.cran_packages:
            self._install_cran_packages(env_prefix, r_cfg.cran_packages)

        # Optional add-ons (these add significant install time)
        if r_cfg.addons.get("adbc"):
            import time as _time
            t0 = _time.monotonic()
            self._install_adbc(env_prefix, config.env_name)
            elapsed = _time.monotonic() - t0
            logger.info("    ADBC step took %.0fs", elapsed)

        if r_cfg.addons.get("duckdb"):
            import time as _time
            t0 = _time.monotonic()
            try:
                self._install_duckdb(env_prefix, config.env_name)
            except Exception as exc:
                msg = f"DuckDB addon install failed (non-fatal): {exc}"
                logger.warning("    %s", msg)
                warnings.append(msg)
            elapsed = _time.monotonic() - t0
            logger.info("    DuckDB step took %.0fs", elapsed)

        version = self._get_r_version(env_prefix)

        age_warning = self._check_r_version_age(env_prefix)
        if age_warning:
            warnings.append(age_warning)
            logger.warning("  %s", age_warning)

        return PluginResult(
            success=True, language="r",
            version=version, env_prefix=env_prefix,
            warnings=warnings,
        )

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        errors: list[str] = []

        r_bin = os.path.join(env_prefix, "bin", "R")
        if not os.path.isfile(r_bin):
            errors.append(f"R binary not found at {r_bin}")

        r_home = os.path.join(env_prefix, "lib", "R")
        if not os.path.isdir(r_home):
            errors.append(f"R_HOME not found at {r_home}")

        if not errors:
            try:
                result = run_cmd(
                    [r_bin, "--vanilla", "--quiet", "-e", "cat(R.version.string)"],
                    description="Validate R",
                )
                logger.info("  R validation: %s", result.stdout.strip())
            except Exception as exc:
                errors.append(f"R execution failed: {exc}")

        return PluginResult(
            success=len(errors) == 0, language="r",
            version=self._get_r_version(env_prefix),
            env_prefix=env_prefix, errors=errors,
        )

    # -----------------------------------------------------------------
    # Internal methods
    # -----------------------------------------------------------------

    def _fix_symlinks(self, env_prefix: str) -> None:
        """Fix missing symlinks for libz.so and liblzma.so."""
        logger.info("  Fixing library symlinks...")
        lib_dir = os.path.join(env_prefix, "lib")
        if not os.path.isdir(lib_dir):
            return

        created = 0
        for base in ("z", "lzma"):
            link_name = os.path.join(lib_dir, f"lib{base}.so")
            if os.path.exists(link_name):
                continue
            # Find the versioned .so
            candidates = [
                f for f in os.listdir(lib_dir)
                if f.startswith(f"lib{base}.so.") and not f.endswith(".py")
            ]
            if candidates:
                target = sorted(candidates)[0]
                os.symlink(target, link_name)
                logger.debug("    Created symlink: lib%s.so -> %s", base, target)
                created += 1

        if created == 0:
            logger.info("    Symlinks already configured")
        else:
            logger.info("    Created %d symlink(s)", created)

    def _install_cran_packages(self, env_prefix: str, packages: list[str]) -> None:
        """Install CRAN packages via R's install.packages()."""
        logger.info("  Installing CRAN packages...")
        r_bin = os.path.join(env_prefix, "bin", "R")

        latest = [p for p in packages if "==" not in str(p)]
        versioned = {}
        for p in packages:
            if "==" in str(p):
                name, ver = str(p).split("==", 1)
                versioned[name.strip()] = ver.strip()

        if latest:
            pkg_vec = ", ".join(f'"{p}"' for p in latest)
            r_code = textwrap.dedent(f"""\
                pkgs <- c({pkg_vec})
                installed <- rownames(installed.packages())
                missing <- setdiff(pkgs, installed)
                if (length(missing) > 0) {{
                    message("Installing CRAN packages: ", paste(missing, collapse=", "))
                    install.packages(missing, repos="https://cloud.r-project.org", quiet=TRUE)
                }} else {{
                    message("All CRAN packages already installed")
                }}
            """)
            run_cmd([r_bin, "--vanilla", "--quiet", "-e", r_code],
                    description="Install CRAN packages (latest)")

        if versioned:
            for pkg_name, pkg_version in versioned.items():
                r_code = textwrap.dedent(f"""\
                    if (!requireNamespace("remotes", quietly=TRUE))
                        install.packages("remotes", repos="https://cloud.r-project.org", quiet=TRUE)
                    remotes::install_version("{pkg_name}", version="{pkg_version}",
                        repos="https://cloud.r-project.org", quiet=TRUE, upgrade="never")
                """)
                run_cmd([r_bin, "--vanilla", "--quiet", "-e", r_code],
                        description=f"Install CRAN {pkg_name}=={pkg_version}")

    @retry(max_attempts=2, delay=3)
    def _install_adbc(self, env_prefix: str, env_name: str) -> None:
        """Install ADBC Snowflake driver for R."""
        logger.info("  Installing ADBC Snowflake driver...")
        r_bin = os.path.join(env_prefix, "bin", "R")

        # Go compiler is needed for adbcsnowflake build
        from ..shared.conda_env import get_missing_packages as _missing
        missing = _missing(env_name, ["go", "libadbc-driver-snowflake"])
        if missing:
            from ..shared.conda_env import _micromamba_bin
            run_cmd(
                [_micromamba_bin(), "install", "-y", "-n", env_name, "-c", "conda-forge"] + missing,
                description="Install ADBC conda deps",
            )

        go_bin = os.path.join(env_prefix, "bin", "go")
        r_code = textwrap.dedent(f"""\
            Sys.setenv(GO_BIN = "{go_bin}")
            if (!requireNamespace("adbcdrivermanager", quietly=TRUE)) {{
                install.packages("adbcdrivermanager", repos="https://cloud.r-project.org", quiet=TRUE)
            }}
            if (!requireNamespace("adbcsnowflake", quietly=TRUE)) {{
                install.packages("adbcsnowflake", repos="https://community.r-multiverse.org")
            }}
            if (!requireNamespace("adbcsnowflake", quietly=TRUE))
                stop("Failed to install adbcsnowflake")
            message("ADBC Snowflake driver ready")
        """)
        run_cmd([r_bin, "--vanilla", "--quiet", "-e", r_code],
                description="Install R ADBC packages")
        logger.info("    ADBC installation complete")

    def _install_duckdb(self, env_prefix: str, env_name: str) -> None:
        """Install DuckDB with Snowflake extension for R.

        DuckDB's built-in INSTALL command uses HTTP by default, which
        SPCS blocks.  We download extension binaries directly via
        Python's urllib (HTTPS) and place them in R's DuckDB extension
        directory, then verify with LOAD.
        """
        import gzip
        import urllib.request

        logger.info("  Installing DuckDB with Snowflake extension...")

        from ..shared.conda_env import get_missing_packages as _missing, _micromamba_bin
        missing = _missing(env_name, ["r-duckdb", "r-dbplyr"])
        if missing:
            run_cmd(
                [_micromamba_bin(), "install", "-y", "-n", env_name, "-c", "conda-forge"] + missing,
                description="Install DuckDB R packages",
            )

        # Detect DuckDB version from conda package metadata (avoids R prompt artifacts)
        r_bin = os.path.join(env_prefix, "bin", "R")
        import re
        import json as _json
        duckdb_ver = None
        meta_path = os.path.join(env_prefix, "conda-meta")
        if os.path.isdir(meta_path):
            for fname in os.listdir(meta_path):
                if fname.startswith("r-duckdb-") and fname.endswith(".json"):
                    try:
                        with open(os.path.join(meta_path, fname)) as fh:
                            meta = _json.load(fh)
                        ver_match = re.search(r'(\d+\.\d+\.\d+)', meta.get("version", ""))
                        if ver_match:
                            duckdb_ver = f"v{ver_match.group(1)}"
                    except Exception:
                        pass
                    break
        if not duckdb_ver:
            duckdb_ver = "v1.4.4"
        logger.info("    DuckDB version: %s", duckdb_ver)

        ext_dir = os.path.expanduser(
            f"~/.local/share/R/duckdb/extensions/{duckdb_ver}/linux_amd64"
        )
        os.makedirs(ext_dir, exist_ok=True)

        extensions = [
            ("httpfs", f"https://extensions.duckdb.org/{duckdb_ver}/linux_amd64/httpfs.duckdb_extension.gz"),
            ("snowflake", f"https://community-extensions.duckdb.org/{duckdb_ver}/linux_amd64/snowflake.duckdb_extension.gz"),
        ]

        for ext_name, url in extensions:
            dest = os.path.join(ext_dir, f"{ext_name}.duckdb_extension")
            if os.path.isfile(dest):
                logger.info("    %s already installed", ext_name)
                continue
            logger.info("    Downloading %s from %s", ext_name, url)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sfnb-multilang"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    gz_data = resp.read()
                with open(dest, "wb") as f:
                    f.write(gzip.decompress(gz_data))
                logger.info("    %s installed (%d bytes)", ext_name, os.path.getsize(dest))
            except Exception as exc:
                raise RuntimeError(f"Failed to download {ext_name}: {exc}") from exc

        # Verify by loading in R
        r_code = textwrap.dedent("""\
            library(DBI); library(duckdb)
            con <- dbConnect(duckdb::duckdb(), dbdir=":memory:")
            dbExecute(con, "SET autoinstall_known_extensions=false")
            dbExecute(con, "LOAD httpfs")
            dbExecute(con, "LOAD snowflake")
            message("DuckDB Snowflake extension loaded and verified")
            dbDisconnect(con)
        """)
        run_cmd([r_bin, "--vanilla", "--quiet", "-e", r_code],
                description="Verify DuckDB extensions")
        logger.info("    DuckDB installation complete")

    def _check_r_version_age(self, env_prefix: str) -> str | None:
        """Warn if the installed r-base conda package was built very recently.

        conda-forge typically needs 2-4 weeks after a new R release to
        rebuild all downstream R packages.  A freshly built r-base means
        some packages may not yet be available at matching versions.
        """
        import json as _json
        from datetime import datetime, timezone

        meta_dir = os.path.join(env_prefix, "conda-meta")
        if not os.path.isdir(meta_dir):
            return None

        for fname in os.listdir(meta_dir):
            if not fname.startswith("r-base-") or not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(meta_dir, fname)) as fh:
                    meta = _json.load(fh)
                ts = meta.get("timestamp")
                if not ts:
                    return None
                build_date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                age_days = (datetime.now(tz=timezone.utc) - build_date).days
                version = meta.get("version", "unknown")
                if age_days < 30:
                    return (
                        f"R {version} was built on conda-forge only "
                        f"{age_days} day(s) ago. Some R packages may not "
                        f"yet be rebuilt for this version. If you hit "
                        f"dependency errors during model registration or "
                        f"SPCS inference, pin an older R version in your "
                        f"YAML config (e.g. r_version: '4.5.2')."
                    )
            except Exception:
                pass
            break

        return None

    def _get_r_version(self, env_prefix: str) -> str:
        r_bin = os.path.join(env_prefix, "bin", "R")
        if not os.path.isfile(r_bin):
            return "unknown"
        try:
            result = run_cmd(
                [r_bin, "--version"],
                description="Get R version",
                check=False,
            )
            first_line = (result.stdout or "").split("\n")[0]
            return first_line.strip()
        except Exception:
            return "unknown"
