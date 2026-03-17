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

    # Install R packages (snowflakeR, RSnowflake, extras):
    from sfnb_setup import install_r_packages
    install_r_packages(config="r_smoke_test_config.yaml")

    # General (any language):
    from sfnb_setup import install
    install(languages=["r", "scala"])

    # Test public-user experience from within the monorepo:
    import os; os.environ["SFNB_PUBLIC_MODE"] = "1"
    from sfnb_setup import install_r
    install_r()
"""
import importlib
import glob
import os
import subprocess
import sys
import tempfile
import urllib.request


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

    try:
        _found = importlib.util.find_spec("sfnb_multilang") is not None
    except OSError:
        _found = False
    if not _found:
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


# ---------------------------------------------------------------------------
# R package installer (tarball / URL / pak fallback)
# ---------------------------------------------------------------------------

_GITHUB_FALLBACKS = {
    "snowflakeR": "Snowflake-Labs/snowflakeR",
    "RSnowflake": "Snowflake-Labs/RSnowflake",
}


def _resolve_tarball(pkg: str, src: str | None) -> str | None:
    """Resolve a tarball source to a local file path.

    Resolution order:
      1. URL  -> download to temp dir
      2. Local path -> use directly
      3. Recursive glob search for <pkg>_*.tar.gz
    On URL/path failure, falls through to the next strategy.
    Returns a local path or None if nothing found.
    """
    if src:
        if src.startswith(("http://", "https://")):
            dest = os.path.join(tempfile.gettempdir(), os.path.basename(src))
            print(f"  {pkg}: downloading {src}")
            try:
                urllib.request.urlretrieve(src, dest)
                return dest
            except Exception as exc:
                print(f"  {pkg}: URL download failed ({exc}), "
                      "trying local search...")
        elif os.path.exists(src):
            return src
        else:
            print(f"  {pkg}: WARNING configured path not found: {src}")

    hits = sorted(glob.glob(f"**/{pkg}_*.tar.gz", recursive=True))
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    print(f"  {pkg}: found {len(hits)} tarballs, using newest")
    return hits[-1]


def _r_install(path: str):
    """Call R's install.packages() via rpy2."""
    from rpy2.robjects import r as R
    R(f'install.packages("{path}", repos = NULL, type = "source")')


def _r_pak_install(repo: str):
    """Fall back to pak::pak() via rpy2."""
    from rpy2.robjects import r as R
    R('options(repos = c(CRAN = "https://cloud.r-project.org"), '
      'pkg.sysreqs = FALSE)')
    R('if (!requireNamespace("pak", quietly = TRUE)) '
      'install.packages("pak", type = "source", quiet = TRUE)')
    R(f'pak::pak("{repo}", ask = FALSE, upgrade = FALSE)')


def _r_pkg_version(pkg: str) -> str:
    from rpy2.robjects import r as R
    try:
        return str(R(f'as.character(packageVersion("{pkg}"))')[0])
    except Exception:
        return "NOT FOUND"


def install_r_packages(config: str | None = None, packages: list[str] | None = None):
    """Install R packages from tarballs (URL/local/search) with pak fallback.

    Parameters
    ----------
    config : str, optional
        Path to a YAML config with a ``languages.r.tarballs`` section.
        If None or the file doesn't exist, falls back to GitHub via pak.
    packages : list[str], optional
        Core packages to install. Defaults to ["snowflakeR", "RSnowflake"].
        Pass e.g. ["snowflakeR"] for notebooks that only need one package.
    """
    if packages is None:
        packages = ["snowflakeR", "RSnowflake"]

    tarballs: dict[str, str] = {}
    if config and os.path.exists(config):
        import yaml
        with open(config) as f:
            cfg = yaml.safe_load(f) or {}
        tarballs = (cfg.get("languages", {})
                       .get("r", {})
                       .get("tarballs")) or {}

    core = [p for p in packages if p in _GITHUB_FALLBACKS]
    extras = [p for p in tarballs if p not in packages]

    for pkg in core:
        path = _resolve_tarball(pkg, tarballs.get(pkg))
        if path:
            print(f"  {pkg} <- {path}")
            _r_install(path)
        elif pkg in _GITHUB_FALLBACKS:
            print(f"  {pkg}: no tarball -- falling back to GitHub")
            _r_pak_install(_GITHUB_FALLBACKS[pkg])
        else:
            print(f"  {pkg}: WARNING could not resolve")

    for pkg in extras:
        path = _resolve_tarball(pkg, tarballs.get(pkg))
        if path:
            print(f"  {pkg} <- {path}")
            _r_install(path)
        else:
            print(f"  {pkg}: WARNING could not resolve tarball")

    print("\nInstalled versions:")
    for pkg in core + extras:
        print(f"  {pkg} {_r_pkg_version(pkg)}")
