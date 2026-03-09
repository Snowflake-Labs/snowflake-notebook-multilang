"""Typed exception hierarchy for the multi-language toolkit.

All toolkit exceptions inherit from ToolkitError so callers can catch
a single base class while still distinguishing specific failure modes.
"""

from __future__ import annotations


class ToolkitError(Exception):
    """Base exception for all toolkit errors."""


class PreflightError(ToolkitError):
    """One or more preflight checks failed (disk space, Python version, etc.)."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        summary = "; ".join(errors)
        super().__init__(f"Preflight checks failed: {summary}")


class PackageConflictError(ToolkitError):
    """Two or more plugins request irreconcilable versions of the same package."""

    def __init__(self, package: str, details: list[str]):
        self.package = package
        self.details = details
        lines = "\n".join(f"  {d}" for d in details)
        super().__init__(
            f"Version conflict for '{package}':\n{lines}\n"
            "These constraints cannot be satisfied simultaneously.\n"
            "Fix: align versions in config, or install conflicting "
            "languages in separate environments."
        )


class PackageInstallError(ToolkitError):
    """A package installation step failed (conda, pip, CRAN, Maven, Julia Pkg)."""

    def __init__(self, message: str, package: str = "", language: str = ""):
        self.package = package
        self.language = language
        super().__init__(message)


class NetworkRuleError(ToolkitError):
    """Failed to create or apply a network rule / EAI."""


class PluginError(ToolkitError):
    """A language plugin's post_install or validate_install step failed."""

    def __init__(self, language: str, phase: str, message: str):
        self.language = language
        self.phase = phase
        super().__init__(f"[{language}] {phase}: {message}")


class ConfigError(ToolkitError):
    """Invalid or missing configuration."""
