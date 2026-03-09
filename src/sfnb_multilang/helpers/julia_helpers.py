"""
Julia Environment Helpers for Snowflake Workspace Notebooks

This module provides:
- Julia startup via JuliaCall (in-process, shared memory)
- %%julia cell magic and %julia line magic for Julia execution
- -i / -o flags for Python <-> Julia variable transfer
- DataFrame interop (pandas <-> DataFrames.jl)
- Snowflake session credential injection
- Environment diagnostics

Architecture:
    Python Kernel  <-->  Julia Runtime (in-process via JuliaCall)
                            - DataFrames.jl, CSV.jl, Arrow.jl
                            - PythonCall.jl (Julia -> Python callbacks)
                            - ODBC.jl (optional, for direct Snowflake)
                            - User Julia code execution

Usage:
    from julia_helpers import setup_julia_environment

    result = setup_julia_environment()

    # Then in notebook cells:
    # %%julia
    # using DataFrames
    # df = DataFrame(x=1:10, y=rand(10))
    # describe(df)

    # With variable transfer:
    # %%julia -i py_data -o result
    # result = describe(py_data)

After setup with Snowflake session:
    from julia_helpers import inject_session_credentials
    inject_session_credentials(session)

    # %%julia
    # using PythonCall
    # session = pyimport("__main__").session
    # result = session.sql("SELECT CURRENT_USER()").to_pandas()
"""

import os
import sys
import json
import subprocess
import shutil
import shlex
import time
from typing import Optional, Dict, Any, List


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_METADATA_FILE = os.path.expanduser(
    "~/julia_depot/julia_env_metadata.json"
)


def _resolve_julia_env_prefix() -> str:
    """Return the Julia environment prefix.

    Checks ``~/.workspace_env_prefix`` first (written by the combined
    setup_workspace_environment.sh script), then falls back to the
    per-language default.
    """
    marker = os.path.expanduser("~/.workspace_env_prefix")
    if os.path.isfile(marker):
        prefix = open(marker).read().strip()
        if prefix and os.path.isdir(prefix):
            return prefix
    return "/root/.local/share/mamba/envs/julia_env"


DEFAULT_JULIA_ENV_PREFIX = _resolve_julia_env_prefix()


class MagicExecutionError(Exception):
    """Raised when a %%julia magic cell fails.

    Raising this (rather than just printing to stderr) ensures that
    Jupyter's "Run All" stops at the failing cell instead of silently
    continuing.
    """


_julia_state = {
    "initialized": False,
    "jl": None,
    "magic_registered": False,
    "metadata": None,
}


# =============================================================================
# Environment Setup
# =============================================================================

def setup_julia_environment(
    register_magic: bool = True,
    install_juliacall: bool = True,
    metadata_file: Optional[str] = None,
    _reloaded: bool = False,
) -> Dict[str, Any]:
    """
    Configure the Python environment for Julia execution via JuliaCall.

    This function:
    1. Loads environment metadata from the setup script
    2. Sets environment variables (JULIA_DEPOT_PATH, etc.)
    3. Installs JuliaCall if needed
    4. Imports JuliaCall and initialises Julia
    5. Loads core Julia packages
    6. Registers the %%julia IPython magic

    Re-running this function always picks up the latest code from
    disk (auto-reloads the module), so helper functions like
    snowflake_query() are refreshed without a container restart.

    Args:
        register_magic: Register the %%julia magic (default True)
        install_juliacall: pip-install juliacall if missing (True)
        metadata_file: Path to julia_env_metadata.json (auto)

    Returns:
        Dict with setup status, Julia version, and any errors.
    """
    if not _reloaded:
        import importlib
        fresh = importlib.reload(sys.modules[__name__])
        return fresh.setup_julia_environment(
            register_magic=register_magic,
            install_juliacall=install_juliacall,
            metadata_file=metadata_file,
            _reloaded=True,
        )

    result = {
        "success": False,
        "julia_version": None,
        "depot_path": None,
        "errors": [],
        "warnings": [],
        "timing": {},
    }

    t_start = time.time()

    # Warn if JVM is already running -- Snowpark Scala session creation
    # after Julia init causes SIGSEGV due to signal handler conflict.
    try:
        import jpype
        if jpype.isJVMStarted():
            import warnings
            warnings.warn(
                "JVM is already running. If you need "
                "bootstrap_snowpark_scala(), call it BEFORE "
                "setup_julia_environment() to avoid a kernel crash "
                "(SIGSEGV from JVM/Julia signal handler conflict). "
                "Restart kernel and reorder cells if needed.",
                stacklevel=2,
            )
            result["warnings"].append(
                "JVM running before Julia init -- Snowpark bootstrap "
                "must happen before this call to avoid SIGSEGV."
            )
    except ImportError:
        pass

    # -------------------------------------------------------------------------
    # Step 1: Load metadata
    # -------------------------------------------------------------------------
    print("Step 1: Loading environment metadata...")
    metadata = _load_metadata(metadata_file)
    if metadata is None:
        result["errors"].append(
            "Metadata file not found. Did you run setup_julia_environment.sh?"
        )
        return result
    _julia_state["metadata"] = metadata
    result["depot_path"] = metadata.get("julia_depot_path")

    # -------------------------------------------------------------------------
    # Step 2: Configure environment variables
    # -------------------------------------------------------------------------
    print("Step 2: Configuring environment...")
    _configure_environment(metadata)

    # -------------------------------------------------------------------------
    # Step 3: Install JuliaCall
    # -------------------------------------------------------------------------
    if install_juliacall:
        print("Step 3: Ensuring JuliaCall is installed...")
        _ensure_juliacall()
    else:
        print("Step 3: Skipping JuliaCall install check")

    # -------------------------------------------------------------------------
    # Step 4: Import JuliaCall and initialise Julia
    # -------------------------------------------------------------------------
    print("Step 4: Initialising Julia runtime (this may take a moment)...")
    t_init = time.time()
    try:
        from juliacall import Main as jl
        _julia_state["jl"] = jl
        _julia_state["initialized"] = True
        result["julia_version"] = str(jl.seval("string(VERSION)"))
        result["timing"]["julia_init_seconds"] = round(time.time() - t_init, 1)
        print(f"  Julia {result['julia_version']} initialised in "
              f"{result['timing']['julia_init_seconds']}s")
    except Exception as e:
        result["errors"].append(f"JuliaCall import failed: {e}")
        print(f"  ERROR: {e}")
        return result

    # -------------------------------------------------------------------------
    # Step 5: Load core Julia packages
    # -------------------------------------------------------------------------
    print("Step 5: Loading Julia packages...")
    t_pkg = time.time()
    try:
        jl.seval("using DataFrames, Statistics")
        print("  Loaded: DataFrames, Statistics")
    except Exception as e:
        result["warnings"].append(f"Core package loading failed: {e}")
        print(f"  WARNING: {e}")

    # Load helper functions into Julia
    _load_julia_helpers(jl)
    result["timing"]["package_load_seconds"] = round(time.time() - t_pkg, 1)

    # -------------------------------------------------------------------------
    # Step 6: Register magic
    # -------------------------------------------------------------------------
    if register_magic:
        print("Step 6: Registering %%julia magic...")
        _register_magic(jl)
    else:
        print("Step 6: Skipping magic registration")

    result["success"] = True
    result["timing"]["total_seconds"] = round(time.time() - t_start, 1)

    print("")
    print("=" * 60)
    print(f"Julia environment ready! (v{result['julia_version']})")
    print(f"  Total setup time: {result['timing']['total_seconds']}s")
    print(f"  Depot: {result['depot_path']}")
    print("  Use %%julia cells to write Julia code.")
    print("=" * 60)

    return result


# =============================================================================
# Metadata Loading
# =============================================================================

def _load_metadata(metadata_file: Optional[str] = None) -> Optional[Dict]:
    """Load environment metadata written by setup_julia_environment.sh."""
    search_paths = []
    if metadata_file:
        search_paths.append(metadata_file)
    search_paths.extend([
        DEFAULT_METADATA_FILE,
        os.path.expanduser("~/julia_depot/julia_env_metadata.json"),
    ])

    persistent_dir = os.environ.get("PERSISTENT_DIR", "")
    if persistent_dir:
        search_paths.append(os.path.join(
            persistent_dir, "julia_depot",
            "julia_env_metadata.json",
        ))

    for path in search_paths:
        if os.path.isfile(path):
            with open(path) as f:
                metadata = json.load(f)
            print(f"  Loaded metadata from {path}")
            return metadata

    return None


# =============================================================================
# Environment Configuration
# =============================================================================

def _configure_environment(metadata: Dict):
    """Set environment variables before JuliaCall import."""
    julia_bin = metadata.get("julia_bin", "")
    depot_path = metadata.get("julia_depot_path", "")
    project_path = metadata.get("julia_project", "")
    sysimage = metadata.get("sysimage_path")
    threads = metadata.get("juliacall_threads", "auto")
    optimize = metadata.get("juliacall_optimize", 2)

    env_prefix = metadata.get("env_prefix", DEFAULT_JULIA_ENV_PREFIX)
    if env_prefix and os.path.isdir(env_prefix):
        current_path = os.environ.get("PATH", "")
        bin_dir = os.path.join(env_prefix, "bin")
        if bin_dir not in current_path:
            os.environ["PATH"] = f"{bin_dir}:{current_path}"
            print(f"  Added {bin_dir} to PATH")

    if julia_bin and os.path.isfile(julia_bin):
        os.environ["PYTHON_JULIACALL_EXE"] = julia_bin
        print(f"  PYTHON_JULIACALL_EXE = {julia_bin}")

    if project_path and os.path.isdir(project_path):
        os.environ["PYTHON_JULIACALL_PROJECT"] = project_path
        print(f"  PYTHON_JULIACALL_PROJECT = {project_path}")

    if depot_path:
        os.environ["JULIA_DEPOT_PATH"] = depot_path
        print(f"  JULIA_DEPOT_PATH = {depot_path}")

    if sysimage and os.path.isfile(sysimage):
        os.environ["PYTHON_JULIACALL_SYSIMAGE"] = sysimage
        print(f"  PYTHON_JULIACALL_SYSIMAGE = {sysimage}")

    os.environ["PYTHON_JULIACALL_THREADS"] = str(threads)
    os.environ["PYTHON_JULIACALL_OPTIMIZE"] = str(optimize)
    # "no" reduces signal handler conflicts with the JVM (SIGSEGV).
    # Trade-off: Julia loses its own stack overflow protection.
    # Revert to "yes" if Julia-only notebooks show instability.
    os.environ["PYTHON_JULIACALL_HANDLE_SIGNALS"] = "no"
    os.environ["PYTHON_JULIACALL_STARTUP_FILE"] = "no"

    # Bypass the Julia package server — SPCS DNS cannot resolve
    # storage.julialang.net.  With JULIA_PKG_SERVER="" Julia Pkg
    # falls back to Git clones from GitHub.
    os.environ["JULIA_PKG_SERVER"] = ""


def _ensure_juliacall():
    """Install juliacall via pip if not present."""
    try:
        import juliacall  # noqa: F401
        print("  JuliaCall already installed")
    except ImportError:
        print("  Installing JuliaCall...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "juliacall", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("  JuliaCall installed")


# =============================================================================
# Julia Helper Functions (loaded into Julia runtime)
# =============================================================================

def _load_julia_helpers(jl):
    """Define helper functions in Julia for use by the magic and notebooks."""
    jl.seval("""
    # Helper: query Snowflake via Python Snowpark proxy
    function snowflake_query(sql::String; session=nothing)
        PythonCall = Base.require(Base.PkgId(
            Base.UUID("6099a3de-0909-46bc-b1f4-468b9a2dfc0d"), "PythonCall"))
        pyimport = PythonCall.pyimport

        if session === nothing
            main = pyimport("__main__")
            session = main.session
        end
        pdf = session.sql(sql).to_pandas()
        # Wrap raw Py as PyPandasDataFrame (Tables.jl-compatible)
        wrapped = PythonCall.PyPandasDataFrame(pdf)
        return DataFrames.DataFrame(wrapped)
    end

    # Helper: display DataFrame as text (for output capture)
    function _show_df(df; max_rows=20)
        show(stdout, MIME("text/plain"), first(df, max_rows); allcols=true)
        println()
    end
    """)


# =============================================================================
# IPython Magic Registration
# =============================================================================

def _register_magic(jl):
    """Register %%julia cell magic with IPython."""
    try:
        from IPython.core.magic import (
            Magics, magics_class, cell_magic, line_magic, needs_local_scope
        )
        from IPython import get_ipython
    except ImportError:
        print("  WARNING: IPython not available; magic not registered")
        return

    ip = get_ipython()
    if ip is None:
        print("  WARNING: No IPython shell; magic not registered")
        return

    @magics_class
    class JuliaMagics(Magics):
        """IPython magics for Julia execution via JuliaCall."""

        def __init__(self, shell, jl_module):
            super().__init__(shell)
            self._jl = jl_module

        @cell_magic
        @needs_local_scope
        def julia(self, line, cell, local_ns=None):
            """Execute Julia code in the embedded runtime.

            Usage:
                %%julia
                x = rand(3, 4)
                println(sum(x))

            Flags:
                -i var1,var2   Push Python variables into Julia
                -o var1,var2   Pull Julia variables into Python
                --silent       Suppress output
                --time         Print execution wall-clock time
            """
            args = _parse_magic_flags(line)

            # Push Python -> Julia
            if args["inputs"]:
                self._push_vars(args["inputs"], local_ns)

            t0 = time.time()
            try:
                self._jl.seval(cell)
            except Exception as e:
                elapsed = time.time() - t0
                if args["time"]:
                    print(f"\n[wall time: {elapsed:.2f}s]")
                raise MagicExecutionError(str(e)) from e

            elapsed = time.time() - t0
            if args["time"]:
                print(f"\n[wall time: {elapsed:.2f}s]")

            # Pull Julia -> Python
            if args["outputs"]:
                self._pull_vars(args["outputs"])

        @line_magic
        def julia_line(self, line):
            """Execute a single Julia expression: %julia println("hello")"""
            if not line.strip():
                return
            try:
                result = self._jl.seval(line)
                if result is not None:
                    return result
            except Exception as e:
                raise MagicExecutionError(str(e)) from e

        # -- internal ---------------------------------------------------------

        def _push_vars(self, names: List[str], local_ns):
            """Transfer Python variables into Julia Main."""
            ns = self.shell.user_ns
            if local_ns:
                ns = {**ns, **local_ns}

            for name in names:
                if name not in ns:
                    print(f"Warning: '{name}' not found, "
                          "skipping")
                    continue
                value = ns[name]
                try:
                    setattr(self._jl, name, value)
                except Exception as e:
                    print(f"Warning: could not push "
                          f"'{name}' to Julia: {e}")

        def _pull_vars(self, names: List[str]):
            """Transfer Julia variables into Python namespace."""
            for name in names:
                try:
                    value = getattr(self._jl, name)
                    self.shell.user_ns[name] = value
                except Exception as e:
                    print(f"Warning: could not pull "
                          f"'{name}' from Julia: {e}")

    ip.register_magics(JuliaMagics(ip, jl))
    _julia_state["magic_registered"] = True
    print("  Registered %%julia cell magic and %julia_line line magic")


def _parse_magic_flags(line: str) -> Dict[str, Any]:
    """Parse -i, -o, --silent, --time flags from the magic line."""
    args = {"inputs": [], "outputs": [], "silent": False, "time": False}
    if not line.strip():
        return args

    tokens = shlex.split(line)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-i" and i + 1 < len(tokens):
            args["inputs"] = [
                v.strip() for v in tokens[i + 1].split(",")
                if v.strip()
            ]
            i += 2
        elif tok == "-o" and i + 1 < len(tokens):
            args["outputs"] = [
                v.strip() for v in tokens[i + 1].split(",")
                if v.strip()
            ]
            i += 2
        elif tok == "--silent":
            args["silent"] = True
            i += 1
        elif tok == "--time":
            args["time"] = True
            i += 1
        else:
            i += 1
    return args


# =============================================================================
# Snowflake Credential Injection
# =============================================================================

def inject_session_credentials(session) -> None:
    """Extract credentials from a Python Snowpark session for Julia use.

    Sets environment variables that Julia can read via ENV["..."].
    In Workspace Notebooks, Julia ODBC connects via SNOWFLAKE_HOST
    (internal SPCS gateway) using the SPCS OAuth token at
    /snowflake/session/token -- no PAT is needed.

    Args:
        session: Active snowflake.snowpark.Session object.
    """
    os.environ["SNOWFLAKE_ACCOUNT"] = (
        session.get_current_account().strip('"')
    )
    os.environ["SNOWFLAKE_USER"] = (
        session.sql("SELECT CURRENT_USER()").collect()[0][0]
    )
    os.environ["SNOWFLAKE_ROLE"] = (
        session.get_current_role().strip('"')
    )
    os.environ["SNOWFLAKE_DATABASE"] = (
        session.get_current_database().strip('"')
    )
    os.environ["SNOWFLAKE_SCHEMA"] = (
        session.get_current_schema().strip('"')
    )
    os.environ["SNOWFLAKE_WAREHOUSE"] = (
        session.get_current_warehouse().strip('"')
    )
    print("Snowflake credentials injected into env vars.")
    print('  Julia: ENV["SNOWFLAKE_ACCOUNT"], etc.')


# =============================================================================
# Diagnostics
# =============================================================================

def check_environment() -> Dict[str, Any]:
    """Run diagnostic checks on the Julia environment.

    Returns a dict with check names -> pass/fail status and details.
    """
    checks = {}

    # Julia binary
    metadata = _julia_state.get("metadata") or _load_metadata()
    if metadata:
        julia_bin = metadata.get("julia_bin", "")
        checks["julia_binary"] = {
            "ok": os.path.isfile(julia_bin),
            "path": julia_bin,
        }
        checks["julia_depot"] = {
            "ok": os.path.isdir(metadata.get("julia_depot_path", "")),
            "path": metadata.get("julia_depot_path"),
        }
    else:
        checks["metadata"] = {
            "ok": False,
            "detail": "Metadata file not found",
        }

    # JuliaCall installed
    try:
        import juliacall  # noqa: F401
        checks["juliacall"] = {
            "ok": True,
            "version": juliacall.__version__,
        }
    except (ImportError, AttributeError):
        checks["juliacall"] = {"ok": False}

    # Julia initialised
    checks["julia_initialized"] = {"ok": _julia_state["initialized"]}
    checks["magic_registered"] = {"ok": _julia_state["magic_registered"]}

    # Disk space
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        checks["disk_space"] = {
            "ok": free_gb > 1.0,
            "free_gb": round(free_gb, 1),
        }
    except Exception:
        checks["disk_space"] = {"ok": False, "detail": "Could not check"}

    return checks


def print_diagnostics() -> None:
    """Print a formatted diagnostic report."""
    checks = check_environment()
    print("Julia Environment Diagnostics")
    print("=" * 50)
    for name, info in checks.items():
        status = "OK" if info.get("ok") else "FAIL"
        detail = ""
        for k, v in info.items():
            if k != "ok":
                detail += f" {k}={v}"
        print(f"  [{status:4s}] {name}{detail}")
    print("=" * 50)
