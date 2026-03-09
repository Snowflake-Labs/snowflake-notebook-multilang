"""LanguagePlugin abstract base class and package version resolution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..exceptions import PackageConflictError

logger = logging.getLogger("sfnb_multilang.languages.base")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PluginResult:
    """Result of a plugin installation or validation step."""

    success: bool
    language: str
    version: str = ""
    env_prefix: str = ""
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PackageRequest:
    """A package requested by a plugin, with parsed version info."""

    raw: str
    base_name: str
    constraint: str
    requesting_plugin: str


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_CONSTRAINT_OPS = (">=", "<=", "==", "!=", "=", ">", "<")


def parse_package(raw: str, plugin_name: str) -> PackageRequest:
    """Parse 'openjdk=17' into base name + constraint."""
    for sep in _CONSTRAINT_OPS:
        if sep in raw:
            idx = raw.index(sep)
            return PackageRequest(
                raw=raw,
                base_name=raw[:idx],
                constraint=raw[idx:],
                requesting_plugin=plugin_name,
            )
    return PackageRequest(
        raw=raw,
        base_name=raw,
        constraint="",
        requesting_plugin=plugin_name,
    )


def _extract_version(constraint: str) -> str:
    """Strip operator characters from a constraint, returning the version string."""
    return constraint.lstrip(">=<!").strip()


def resolve_version_conflict(
    base_name: str,
    requests: list[PackageRequest],
) -> str:
    """Resolve version constraints from multiple plugins for one package.

    Rules (applied in order):
      1. Identical constraints -> deduplicate silently.
      2. One pinned, others unpinned -> use the pinned version, log INFO.
      3. Compatible range constraints -> tightest bound wins.
      4. Conflicting pins -> raise PackageConflictError.
      5. Range vs. incompatible pin -> raise PackageConflictError.

    Returns the merged package string (e.g. "openjdk=17").
    """
    # Rule 1: all identical
    unique_raws = {r.raw for r in requests}
    if len(unique_raws) == 1:
        return requests[0].raw

    pinned = [
        r for r in requests
        if r.constraint.startswith("=")
        and not r.constraint.startswith(">=")
        and not r.constraint.startswith("==")
    ]
    ranged = [
        r for r in requests
        if r.constraint.startswith(">=") or r.constraint.startswith(">")
    ]
    unpinned = [r for r in requests if not r.constraint]

    # Rule 4: multiple different pins
    unique_pins = {r.constraint for r in pinned}
    if len(unique_pins) > 1:
        raise PackageConflictError(
            base_name,
            [f"{r.requesting_plugin}: {r.raw}" for r in requests],
        )

    # Rule 2: one pin + unpinned only
    if pinned and not ranged:
        winner = pinned[0]
        for r in unpinned:
            logger.info(
                "Package '%s': using pinned version '%s' (from %s); "
                "%s requested unpinned",
                base_name, winner.constraint, winner.requesting_plugin,
                r.requesting_plugin,
            )
        return winner.raw

    # Rule 3: range constraints only -> pick tightest bound
    if ranged and not pinned:
        tightest = max(ranged, key=lambda r: _extract_version(r.constraint))
        for r in requests:
            if r is not tightest:
                logger.info(
                    "Package '%s': using '%s' (from %s); subsumes '%s' from %s",
                    base_name, tightest.raw, tightest.requesting_plugin,
                    r.raw, r.requesting_plugin,
                )
        return tightest.raw

    # Rule 5: pin + range -> check compatibility
    if pinned and ranged:
        pin_version = _extract_version(pinned[0].constraint)
        for r in ranged:
            range_version = _extract_version(r.constraint)
            if pin_version < range_version:
                raise PackageConflictError(
                    base_name,
                    [f"{req.requesting_plugin}: {req.raw}" for req in requests],
                )
        return pinned[0].raw

    # Fallback: first request
    return (pinned or requests)[0].raw


# ---------------------------------------------------------------------------
# Plugin ABC
# ---------------------------------------------------------------------------

class LanguagePlugin(ABC):
    """Base class for language plugins.

    Each plugin declares what it needs (conda packages, network hosts)
    and implements language-specific post-install logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short language identifier, e.g. 'r', 'scala', 'julia'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'R', 'Scala/Java', 'Julia'."""

    @abstractmethod
    def get_conda_packages(self, config: Any) -> list[str]:
        """Return conda-forge packages to install in the shared env."""

    @abstractmethod
    def get_network_hosts(self, config: Any) -> list[dict]:
        """Return hosts needed for EAI network rules.

        Each entry: {"host": str, "port": int, "purpose": str,
                     "required": bool}
        """

    @abstractmethod
    def post_install(self, env_prefix: str, config: Any) -> PluginResult:
        """Run language-specific post-install steps."""

    @abstractmethod
    def validate_install(self, env_prefix: str, config: Any) -> PluginResult:
        """Verify the installation is working."""

    def get_pip_packages(self, config: Any) -> list[str]:
        """Return pip packages to install in the notebook kernel."""
        return []

    def get_helper_module_name(self) -> str:
        """Return the helper module filename."""
        return f"{self.name}_helpers.py"

    def get_metadata(self, env_prefix: str, config: Any) -> dict:
        """Return metadata dict to write to JSON after install."""
        return {}
