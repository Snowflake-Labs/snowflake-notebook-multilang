"""Pre-installation checks: disk space, network connectivity, Python version."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import List

from ..config import ToolkitConfig

logger = logging.getLogger("sfnb_multilang.shared.preflight")

MIN_DISK_SPACE_MB = 2000


@dataclass
class PreflightResult:
    passed: bool = True
    disk_free_mb: int = 0
    python_version: str = ""
    network_reachable: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def check_disk_space(min_mb: int = MIN_DISK_SPACE_MB) -> tuple[bool, int]:
    """Check available disk space on the root filesystem."""
    usage = shutil.disk_usage("/")
    available_mb = usage.free // (1024 * 1024)
    ok = available_mb >= min_mb
    return ok, available_mb


def check_network(endpoints: List[str], timeout: int = 5) -> dict[str, bool]:
    """Check connectivity to a list of HTTPS endpoints.

    Uses GET (not HEAD) since many CDNs/package repos reject HEAD requests.
    Reads only a tiny amount to confirm the connection succeeds.
    """
    results = {}
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sfnb-multilang"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(64)
            results[url] = True
        except Exception:
            results[url] = False
    return results


def run_preflight_checks(config: ToolkitConfig) -> PreflightResult:
    """Run all preflight checks and return a structured result."""
    result = PreflightResult()
    result.python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    logger.info("Running pre-flight checks...")

    # Disk space
    logger.info("Checking disk space...")
    disk_ok, free_mb = check_disk_space()
    result.disk_free_mb = free_mb
    if not disk_ok:
        msg = f"Insufficient disk space: {free_mb}MB available, {MIN_DISK_SPACE_MB}MB required"
        logger.error("  %s", msg)
        result.errors.append(msg)
        result.passed = False
    else:
        logger.info("  Disk space OK: %dMB available", free_mb)

    # Network connectivity
    logger.info("Checking network connectivity...")
    endpoints = ["https://conda.anaconda.org/conda-forge/noarch/repodata.json"]
    if config.r.enabled:
        endpoints.append("https://cloud.r-project.org")
    if config.scala.enabled:
        endpoints.append("https://repo1.maven.org")
    if config.julia.enabled:
        endpoints.append("https://github.com")

    result.network_reachable = check_network(endpoints)
    for url, reachable in result.network_reachable.items():
        if reachable:
            logger.debug("  %s: OK", url)
        else:
            logger.warning("  %s: unreachable (may cause issues)", url)
            result.warnings.append(f"{url}: unreachable")
    logger.info("  Network check complete")

    # Python version
    logger.info("  Python: %s", result.python_version)

    if result.passed:
        logger.info("Pre-flight checks passed")
    else:
        logger.error("Pre-flight checks failed")

    return result
