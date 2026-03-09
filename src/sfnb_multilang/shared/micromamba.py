"""Micromamba download, install, and lifecycle management."""

from __future__ import annotations

import logging
import os
import platform
import stat

from .download import retry
from .subprocess_utils import run_cmd

logger = logging.getLogger("sfnb_multilang.shared.micromamba")


def _get_platform_slug() -> str:
    """Return the micromamba platform slug (e.g. 'linux-64', 'osx-arm64')."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        arch = "64" if machine in ("x86_64", "amd64") else "aarch64"
        return f"linux-{arch}"
    elif system == "darwin":
        arch = "arm64" if machine == "arm64" else "64"
        return f"osx-{arch}"
    else:
        return "linux-64"


@retry(max_attempts=3, delay=5)
def download_micromamba(target_dir: str) -> str:
    """Download the micromamba binary to target_dir/bin/micromamba."""
    bin_dir = os.path.join(target_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    binary_path = os.path.join(bin_dir, "micromamba")

    slug = _get_platform_slug()
    url = f"https://micro.mamba.pm/api/micromamba/{slug}/latest"
    logger.info("Downloading micromamba from %s ...", url)

    # Download and extract in one step (tar -xvj extracts bzip2)
    run_cmd(
        ["sh", "-c", f"curl -Ls --retry 3 --retry-delay 2 '{url}' | tar -xvj -C '{target_dir}' bin/micromamba"],
        description="Download micromamba",
    )

    # Ensure executable
    st = os.stat(binary_path)
    os.chmod(binary_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    logger.info("micromamba installed at %s", binary_path)
    return binary_path


def ensure_micromamba(root: str, force: bool = False) -> str:
    """Return path to micromamba binary, downloading if needed.

    Args:
        root: Directory where micromamba lives (contains bin/micromamba).
        force: Re-download even if already present.

    Returns:
        Absolute path to the micromamba binary.
    """
    root = os.path.expanduser(root)
    binary = os.path.join(root, "bin", "micromamba")

    if os.path.isfile(binary) and os.access(binary, os.X_OK) and not force:
        logger.info("micromamba already installed (skipping)")
        return binary

    return download_micromamba(root)
