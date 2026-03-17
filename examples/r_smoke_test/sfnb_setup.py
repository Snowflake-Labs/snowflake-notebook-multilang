"""Bootstrap sfnb_multilang for Snowflake Workspace Notebooks.

Handles monorepo auto-detection and pip-install fallback so that the
same notebook code works identically in the internal development
monorepo and in standalone public repositories.

Language-agnostic -- works for R, Scala, Julia, or any combination.

DERIVED COPY -- Do not edit directly.
  The canonical source is:
    snowflake-notebook-multilang/src/sfnb_multilang/helpers/sfnb_setup.py
  Edit there, then copy here.

Usage in notebooks:

    # R notebook (one-liner):
    from sfnb_setup import install_r
    install_r()

    # General (any language):
    from sfnb_setup import install
    install(languages=["r", "scala"])

    # Test public-user experience from within the monorepo:
    import os; os.environ["SFNB_PUBLIC_MODE"] = "1"
    from sfnb_setup import install_r
    install_r()
"""
import importlib
import os
import subprocess
import sys


def _detect_monorepo():
    """Walk up from CWD looking for the .monorepo marker file.

    Returns the monorepo root path, or None if not found.
    Respects SFNB_PUBLIC_MODE env var to force public-mode behaviour.
    """
    if os.environ.get("SFNB_PUBLIC_MODE"):
        return None
    d = os.getcwd()
    for _ in range(10):
        if os.path.isfile(os.path.join(d, ".monorepo")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _bootstrap():
    """Ensure sfnb_multilang is importable.

    In the monorepo: adds the local source tree to sys.path and sets
    environment variables for R package source installs.
    Outside the monorepo: pip-installs sfnb-multilang if needed.
    """
    root = _detect_monorepo()
    if root:
        os.environ.setdefault("MONOREPO_ROOT", root)
        src = os.path.join(root, "snowflake-notebook-multilang", "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        for pkg, var in [("snowflakeR", "SNOWFLAKER_PATH"),
                         ("RSnowflake", "RSNOWFLAKE_PATH")]:
            pkg_dir = os.path.join(root, pkg)
            if os.path.isdir(pkg_dir):
                os.environ.setdefault(var, pkg_dir)

    if not importlib.util.find_spec("sfnb_multilang"):
        _GITHUB_URL = (
            "sfnb-multilang @ https://github.com/Snowflake-Labs/"
            "snowflake-notebook-multilang/archive/refs/heads/main.zip"
        )
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", _GITHUB_URL])


_bootstrap()

from sfnb_multilang import install  # noqa: E402 -- re-export after bootstrap


def install_r(**kwargs):
    """Install R and register the %%R magic. Convenience for R-focused notebooks.

    Equivalent to calling install(languages=["r"]) followed by
    r_helpers.setup_r_environment(). Accepts the same keyword arguments
    as sfnb_multilang.install() (e.g. config, r_adbc, r_cran_packages).
    """
    kwargs.setdefault("languages", ["r"])
    install(**kwargs)
    try:
        from sfnb_multilang.helpers.r_helpers import setup_r_environment
    except ImportError:
        from r_helpers import setup_r_environment
    setup_r_environment()
