"""Conda/micromamba environment creation and package management."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .subprocess_utils import run_cmd

logger = logging.getLogger("sfnb_multilang.shared.conda_env")


def _micromamba_bin() -> str:
    """Locate the micromamba binary on PATH or in ~/micromamba/bin."""
    # Check PATH first
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(p, "micromamba")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Fallback
    home_bin = os.path.expanduser("~/micromamba/bin/micromamba")
    if os.path.isfile(home_bin):
        return home_bin
    raise FileNotFoundError("micromamba not found. Run ensure_micromamba() first.")


def env_exists(env_name: str) -> bool:
    """Check whether a named micromamba environment exists."""
    try:
        result = run_cmd(
            [_micromamba_bin(), "env", "list", "--json"],
            description="List environments",
            check=False,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        envs = data.get("envs", [])
        return any(e.endswith(f"/{env_name}") or e.endswith(f"\\{env_name}") for e in envs)
    except Exception:
        return False


def get_env_prefix(env_name: str) -> str:
    """Return the filesystem prefix for a named environment."""
    result = run_cmd(
        [_micromamba_bin(), "env", "list", "--json"],
        description="Get env prefix",
    )
    data = json.loads(result.stdout)
    for env_path in data.get("envs", []):
        if env_path.endswith(f"/{env_name}") or env_path.endswith(f"\\{env_name}"):
            return env_path
    raise FileNotFoundError(f"Environment '{env_name}' not found")


def get_installed_packages(env_name: str) -> dict[str, str]:
    """Return {package_name: version} for the given environment."""
    result = run_cmd(
        [_micromamba_bin(), "list", "-n", env_name, "--json"],
        description="List packages",
    )
    packages = json.loads(result.stdout)
    return {p["name"]: p["version"] for p in packages}


def get_missing_packages(env_name: str, requested: list[str]) -> list[str]:
    """Return packages from requested that are not yet installed."""
    installed = get_installed_packages(env_name)
    missing = []
    for pkg in requested:
        base = pkg.split("=")[0].split(">")[0].split("<")[0]
        if base not in installed:
            missing.append(pkg)
        else:
            logger.debug("  Package already installed: %s (%s)", base, installed[base])
    return missing


def create_or_update_env(
    env_name: str,
    packages: list[str],
    channel: str = "conda-forge",
    force: bool = False,
    ssl_cert_path: str = "",
) -> str:
    """Create or update a micromamba environment. Return the env prefix.

    Args:
        env_name: Name of the environment.
        packages: List of packages with optional version specifiers.
        channel: Conda channel URL or name (e.g. "conda-forge" or an
            Artifactory/Nexus remote repo URL).
        force: If True, reinstall even if packages exist.
        ssl_cert_path: Path to a custom CA certificate bundle for
            corporate TLS inspection proxies.

    Returns:
        Absolute path to the environment prefix.
    """
    mm = _micromamba_bin()
    exists = env_exists(env_name)

    ssl_flags: list[str] = []
    if ssl_cert_path and os.path.isfile(ssl_cert_path):
        ssl_flags = ["--ssl-verify", ssl_cert_path]

    if exists and not force:
        logger.info("Checking installed packages in '%s'...", env_name)
        missing = get_missing_packages(env_name, packages)
        if not missing:
            logger.info("  All conda packages already installed (skipping)")
        else:
            logger.info("  Installing missing packages: %s", " ".join(missing))
            run_cmd(
                [mm, "install", "-y", "-n", env_name, "-c", channel]
                + ssl_flags + missing,
                description="Install missing packages",
            )
    elif exists and force:
        logger.info("Force reinstalling packages in '%s'...", env_name)
        run_cmd(
            [mm, "install", "-y", "-n", env_name, "-c", channel]
            + ssl_flags + packages,
            description="Update environment",
        )
    else:
        logger.info("Creating environment '%s'...", env_name)
        run_cmd(
            [mm, "create", "-y", "-n", env_name, "-c", channel]
            + ssl_flags + packages,
            description="Create environment",
        )

    prefix = get_env_prefix(env_name)
    logger.info("  Environment prefix: %s", prefix)

    # Write prefix for helper auto-discovery
    prefix_file = os.path.expanduser("~/.workspace_env_prefix")
    with open(prefix_file, "w") as f:
        f.write(prefix)
    logger.debug("  Wrote prefix to %s", prefix_file)

    return prefix
