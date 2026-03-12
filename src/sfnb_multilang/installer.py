"""Core installer orchestrator -- coordinates plugins and shared infrastructure."""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import ToolkitConfig
from .exceptions import (
    NetworkRuleError,
    PackageConflictError,
    PreflightError,
    ToolkitError,
)
from .languages import get_enabled_plugins
from .languages.base import (
    LanguagePlugin,
    PackageRequest,
    PluginResult,
    parse_package,
    resolve_version_conflict,
)
from .shared.conda_env import create_or_update_env
from .shared.micromamba import ensure_micromamba
from .shared.preflight import run_preflight_checks
from .shared.subprocess_utils import run_cmd

logger = logging.getLogger("sfnb_multilang.installer")


# ---------------------------------------------------------------------------
# Install report
# ---------------------------------------------------------------------------

@dataclass
class EaiResult:
    success: bool
    sql: str = ""
    sql_file_path: str = ""
    error_message: str = ""


@dataclass
class InstallReport:
    started_at: float = field(default_factory=time.monotonic)
    preflight: Optional[object] = None
    micromamba_path: str = ""
    env_prefix: str = ""
    eai_applied: bool = False
    plugin_results: dict[str, PluginResult] = field(default_factory=dict)
    validation_results: dict[str, PluginResult] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    success: bool = False

    def add_plugin_result(self, name: str, result: PluginResult) -> None:
        self.plugin_results[name] = result

    def add_validation_result(self, name: str, result: PluginResult) -> None:
        self.validation_results[name] = result

    def finalize(self) -> None:
        self.elapsed_seconds = time.monotonic() - self.started_at
        self.success = all(
            r.success for r in self.plugin_results.values()
        ) and all(
            r.success for r in self.validation_results.values()
        )


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

class Installer:
    """Core installer that coordinates plugins and shared infrastructure."""

    def __init__(self, config: ToolkitConfig):
        self.config = config
        self.plugins = get_enabled_plugins(config)

    def install(self, quiet: bool = False) -> InstallReport:
        report = InstallReport()

        if not self.plugins:
            logger.error("No languages enabled. Enable at least one in config or CLI flags.")
            raise ToolkitError("No languages enabled")

        plugin_names = ", ".join(p.display_name for p in self.plugins)
        logger.info("Installing: %s", plugin_names)
        logger.info("")

        # Phase 0: Network rule setup (optional)
        if self.config.network_rule.apply_in_installer:
            logger.info("Phase 0: Network rule configuration")
            eai_result = self._apply_network_rules(report)
            if not eai_result.success:
                return report

        # Phase 1: Preflight
        logger.info("Phase 1: Preflight checks")
        preflight_result = run_preflight_checks(self.config)
        report.preflight = preflight_result
        if not preflight_result.passed:
            raise PreflightError(preflight_result.errors)
        logger.info("")

        # Phase 2: Micromamba
        logger.info("Phase 2: Install/verify micromamba")
        micromamba_path = ensure_micromamba(
            root=self.config.micromamba_root_expanded,
            force=self.config.force_reinstall,
        )
        report.micromamba_path = micromamba_path
        os.environ["PATH"] = (
            os.path.dirname(micromamba_path) + os.pathsep + os.environ.get("PATH", "")
        )
        logger.info("")

        # Phase 3: Merged conda install
        logger.info("Phase 3: Conda environment setup")
        all_conda_packages = self._collect_conda_packages()
        logger.info("  Combined packages: %s", " ".join(all_conda_packages))
        env_prefix = create_or_update_env(
            env_name=self.config.env_name,
            packages=all_conda_packages,
            force=self.config.force_reinstall,
        )
        report.env_prefix = env_prefix
        os.environ["PATH"] = (
            os.path.join(env_prefix, "bin") + os.pathsep + os.environ.get("PATH", "")
        )
        logger.info("")

        # Phase 4: Pip packages
        logger.info("Phase 4: Pip packages for notebook kernel")
        all_pip_packages = self._collect_pip_packages()
        if all_pip_packages:
            self._install_pip_packages(all_pip_packages)
        else:
            logger.info("  No pip packages required")
        logger.info("")

        # Phase 5: Language-specific post-install
        logger.info("Phase 5: Language-specific post-install")
        for plugin in self.plugins:
            logger.info("  Post-install: %s", plugin.display_name)
            result = plugin.post_install(env_prefix, self.config)
            report.add_plugin_result(plugin.name, result)
            if not result.success:
                logger.error("  %s post-install failed: %s", plugin.display_name, result.errors)
        logger.info("")

        # Phase 6: Validation
        logger.info("Phase 6: Validation")
        for plugin in self.plugins:
            result = plugin.validate_install(env_prefix, self.config)
            report.add_validation_result(plugin.name, result)
            if result.success:
                logger.info("  %s: OK", plugin.display_name)
            else:
                logger.warning("  %s: FAILED - %s", plugin.display_name, result.errors)
        logger.info("")

        # Phase 7: Deploy helpers
        logger.info("Phase 7: Deploying helper modules")
        self._deploy_helpers()
        logger.info("")

        # Phase 8: Summary
        report.finalize()
        self._print_summary(report, quiet=quiet)
        return report

    # -----------------------------------------------------------------
    # Package collection with version conflict resolution
    # -----------------------------------------------------------------

    def _collect_conda_packages(self) -> list[str]:
        """Merge conda packages with version conflict resolution."""
        by_base: dict[str, list[PackageRequest]] = {}
        for plugin in self.plugins:
            for raw_pkg in plugin.get_conda_packages(self.config):
                req = parse_package(raw_pkg, plugin.name)
                by_base.setdefault(req.base_name, []).append(req)

        resolved = []
        for base_name, requests in by_base.items():
            if len(requests) == 1:
                resolved.append(requests[0].raw)
            else:
                resolved.append(resolve_version_conflict(base_name, requests))
        return resolved

    def _collect_pip_packages(self) -> list[str]:
        """Merge pip packages with conflict resolution."""
        by_base: dict[str, list[PackageRequest]] = {}
        for plugin in self.plugins:
            for raw_pkg in plugin.get_pip_packages(self.config):
                req = parse_package(raw_pkg, plugin.name)
                by_base.setdefault(req.base_name.lower(), []).append(req)

        resolved = []
        for base_name, requests in by_base.items():
            if len(requests) == 1:
                resolved.append(requests[0].raw)
            else:
                resolved.append(resolve_version_conflict(base_name, requests))
        return resolved

    def _install_pip_packages(self, packages: list[str]) -> None:
        """Install pip packages into the notebook kernel."""
        for pkg in packages:
            logger.info("  Installing pip: %s", pkg)
            try:
                run_cmd(
                    ["python3", "-m", "pip", "install", pkg, "-q"],
                    description=f"pip install {pkg}",
                )
            except Exception:
                logger.warning("  %s: install failed (may still work)", pkg)

    # -----------------------------------------------------------------
    # Network rules (Phase 0)
    # -----------------------------------------------------------------

    def _apply_network_rules(self, report: InstallReport) -> EaiResult:
        """Attempt to create network rule + EAI via Snowpark session."""
        from .network_rules import generate_network_rule_sql

        nr_config = self.config.network_rule
        session = self._get_session()

        account = nr_config.account
        if not account and session:
            try:
                result = session.sql("SELECT CURRENT_ACCOUNT()").collect()
                account = result[0][0]
            except Exception:
                logger.warning("Could not auto-detect account from session")

        sql = generate_network_rule_sql(
            self.config, account=account,
            rule_name=nr_config.rule_name,
            integration_name=nr_config.integration_name,
        )

        if not session:
            return self._export_sql_fallback(sql, nr_config, "No active Snowpark session")

        statements = [
            s.strip() for s in sql.split(";")
            if s.strip() and not s.strip().startswith("--")
        ]

        try:
            for stmt in statements:
                logger.info("Executing: %s...", stmt[:80])
                session.sql(stmt).collect()
                logger.info("  OK")

            if nr_config.grant_to_role:
                grant_sql = (
                    f"GRANT USAGE ON INTEGRATION {nr_config.integration_name} "
                    f"TO ROLE {nr_config.grant_to_role}"
                )
                session.sql(grant_sql).collect()

            logger.info(
                "EAI created: %s\n  Network rule: %s\n"
                "  Enable in Snowsight > Notebook settings > External access",
                nr_config.integration_name, nr_config.rule_name,
            )
            report.eai_applied = True
            return EaiResult(success=True, sql=sql)

        except Exception as exc:
            error_msg = str(exc)
            permission_phrases = (
                "insufficient privileges", "access denied", "not authorized",
                "permission denied", "does not have privilege",
            )
            if any(p in error_msg.lower() for p in permission_phrases):
                return self._export_sql_fallback(
                    sql, nr_config,
                    "Insufficient privileges to create network rule / integration.",
                )
            self._export_sql_fallback(sql, nr_config, error_msg)
            raise NetworkRuleError(f"Unexpected error applying EAI: {error_msg}") from exc

    def _export_sql_fallback(self, sql: str, nr_config, reason: str) -> EaiResult:
        """Write SQL to file and print instructions when execution fails."""
        export_path = nr_config.sql_export_path or "./eai_setup.sql"
        abs_path = os.path.abspath(export_path)

        with open(abs_path, "w") as f:
            f.write(sql)

        sep = "=" * 70
        logger.warning(
            "\n%s\n  NETWORK RULE SETUP REQUIRED\n%s\n\n"
            "  Reason: %s\n\n"
            "  The installer needs external network access but could not\n"
            "  create the required network rule automatically.\n\n"
            "  What to do:\n"
            "  1. Share the file below with your Snowflake administrator\n"
            "  2. They should execute the SQL in a worksheet with ACCOUNTADMIN\n"
            "  3. Enable '%s' on your notebook\n"
            "     via Snowsight > Notebook settings > External access\n"
            "  4. Re-run the installer\n\n"
            "  SQL file saved to: %s\n%s",
            sep, sep, reason, nr_config.integration_name, abs_path, sep,
        )
        print(sql)
        logger.warning(
            "%s\n  Installation halted. Re-run after network access is configured.\n%s",
            sep, sep,
        )
        return EaiResult(success=False, sql=sql, sql_file_path=abs_path, error_message=reason)

    def _get_session(self):
        """Retrieve the active Snowpark session if available."""
        try:
            from snowflake.snowpark.context import get_active_session
            return get_active_session()
        except Exception:
            return None

    # -----------------------------------------------------------------
    # Helper deployment
    # -----------------------------------------------------------------

    def _deploy_helpers(self) -> None:
        """Copy helper modules to the notebook working directory."""
        helpers_dir = Path(__file__).parent / "helpers"
        for plugin in self.plugins:
            module_name = plugin.get_helper_module_name()
            src = helpers_dir / module_name
            if src.exists():
                dst = Path.cwd() / module_name
                shutil.copy2(src, dst)
                logger.info("  Deployed %s to %s", module_name, dst)
            else:
                logger.debug("  Helper not found: %s", src)

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------

    def _print_summary(self, report: InstallReport, quiet: bool = False) -> None:
        lang_statuses = []
        for plugin in self.plugins:
            pr = report.plugin_results.get(plugin.name)
            vr = report.validation_results.get(plugin.name)
            ok = pr and pr.success and vr and vr.success
            version = (pr.version if pr else "") or "?"
            lang_statuses.append((plugin, ok, version))

        if quiet:
            parts = []
            for plugin, ok, version in lang_statuses:
                parts.append(f"{plugin.display_name}: {'OK' if ok else 'FAILED'} ({version})")
            status_word = "complete" if report.success else "finished with errors"
            print(f"Installation {status_word} in {report.elapsed_seconds:.1f}s -- {', '.join(parts)}")
            return

        logger.info("=" * 70)
        logger.info("Installation %s", "Complete!" if report.success else "Finished with errors")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Environment: %s", self.config.env_name)
        logger.info("Prefix:      %s", report.env_prefix)
        logger.info("Duration:    %.1fs", report.elapsed_seconds)
        logger.info("")

        for plugin, ok, version in lang_statuses:
            logger.info("  %s: %s (%s)", plugin.display_name, "OK" if ok else "FAILED", version)

        logger.info("")
        for plugin in self.plugins:
            logger.info(
                "  from %s import setup_%s_environment",
                plugin.get_helper_module_name().replace(".py", ""),
                plugin.name,
            )
        logger.info("")
