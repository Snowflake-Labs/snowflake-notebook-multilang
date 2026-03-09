"""
R Environment Helpers for Snowflake Workspace Notebooks

CANONICAL SOURCE -- This is the single source of truth.
  Copies are distributed to per-package notebook directories:
    - snowflakeR/inst/notebooks/r_helpers.py
    - RSnowflake/inst/notebooks/r_helpers.py
  Edit HERE, then sync to the copies above. The sync scripts
  (sync_snowflakeR_to_public.sh, sync_rsnowflake_to_public.sh)
  copy this file into the public repos automatically.

This module provides helper functions for:
- R environment setup and configuration
- PAT (Programmatic Access Token) management
- Alternative authentication methods (Key Pair, OAuth)
- Environment diagnostics and validation
- R connection management for ADBC
- Output formatting helpers for cleaner display

Usage:
    from r_helpers import setup_r_environment, create_pat, check_environment
    from r_helpers import set_r_console_width  # Adjust R output width
    from r_helpers import init_r_output_helpers  # Load rprint, rview, rglimpse
    from r_helpers import KeyPairAuth, OAuthAuth  # Alternative auth methods
    from r_helpers import init_r_alt_auth  # Load R test functions

After setup, use in R cells:
    rprint(x)      - Print any object cleanly
    rview(df, n)   - View data frame (optional row limit)
    rglimpse(df)   - Glimpse data frame structure
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import json


# =============================================================================
# Configuration
# =============================================================================

def _resolve_r_env_prefix() -> str:
    """Return the R environment prefix.

    Checks ``~/.workspace_env_prefix`` first (written by the combined
    setup_workspace_environment.sh script), then falls back to the
    per-language default.
    """
    marker = os.path.expanduser("~/.workspace_env_prefix")
    if os.path.isfile(marker):
        prefix = open(marker).read().strip()
        if prefix and os.path.isdir(prefix):
            return prefix
    return "/root/.local/share/mamba/envs/r_env"

R_ENV_PREFIX = _resolve_r_env_prefix()
PAT_TOKEN_NAME = "r_adbc_pat"


# =============================================================================
# Environment Setup
# =============================================================================

def setup_r_environment(install_rpy2: bool = True, register_magic: bool = True) -> Dict[str, Any]:
    """
    Configure the Python environment to use R from micromamba.
    
    This function:
    1. Sets PATH and R_HOME environment variables
    2. Optionally installs rpy2 into the notebook kernel
    3. Optionally registers the %%R magic
    
    Args:
        install_rpy2: Whether to install rpy2 (default: True)
        register_magic: Whether to register %%R magic (default: True)
    
    Returns:
        Dict with setup status and any errors
    
    Example:
        >>> result = setup_r_environment()
        >>> if result['success']:
        ...     print("R ready!")
    """
    result = {
        'success': False,
        'r_home': None,
        'r_version': None,
        'rpy2_installed': False,
        'tabulate_installed': False,
        'magic_registered': False,
        'errors': []
    }
    
    # Check if R environment exists
    if not os.path.isdir(R_ENV_PREFIX):
        result['errors'].append(
            f"R environment not found at {R_ENV_PREFIX}. "
            "Run 'bash setup_r_environment.sh' first."
        )
        return result
    
    # Configure environment variables
    os.environ["PATH"] = f"{R_ENV_PREFIX}/bin:" + os.environ.get("PATH", "")
    os.environ["R_HOME"] = f"{R_ENV_PREFIX}/lib/R"
    result['r_home'] = os.environ["R_HOME"]
    
    # Verify R is accessible
    r_path = shutil.which('R')
    if not r_path:
        result['errors'].append("R binary not found in PATH after configuration")
        return result
    
    # Get R version
    try:
        r_version = subprocess.run(
            ['R', '--version'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if r_version.returncode == 0:
            result['r_version'] = r_version.stdout.split('\n')[0]
    except Exception as e:
        result['errors'].append(f"Failed to get R version: {e}")
    
    # Install rpy2 and tabulate if requested (skip when already importable)
    if install_rpy2:
        try:
            import importlib
            _rpy2_ok = importlib.util.find_spec("rpy2") is not None
            _tab_ok = importlib.util.find_spec("tabulate") is not None

            if not _rpy2_ok:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "rpy2", "-q"],
                    check=True, capture_output=True, timeout=120,
                )
            result['rpy2_installed'] = True

            if not _tab_ok:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "tabulate", "-q"],
                    check=True, capture_output=True, timeout=60,
                )
            result['tabulate_installed'] = True
        except subprocess.CalledProcessError as e:
            result['errors'].append(f"Failed to install packages: {e}")
        except subprocess.TimeoutExpired:
            result['errors'].append("Package installation timed out")
    
    # Register magic if requested
    if register_magic and result['rpy2_installed']:
        try:
            ip = get_ipython()
            ip.register_magics(_build_safe_r_magics_class())
            result['magic_registered'] = True
        except NameError:
            result['errors'].append("Not in IPython environment, cannot register magic")
        except Exception as e:
            result['errors'].append(f"Failed to register magic: {e}")
    
    # Configure R console width for better output formatting
    if result['rpy2_installed']:
        try:
            import rpy2.robjects as ro
            ro.r('options(width = 200)')  # Wide console output for notebooks
            ro.r('options(tibble.width = Inf)')  # Show all tibble columns
            ro.r('options(pillar.width = Inf)')  # Pillar (tibble printing) width
            ro.r('options(tibble.print_max = 50)')  # Show more rows
            result['console_configured'] = True
            
            # Load output helpers for cleaner formatting in Workspace Notebooks
            ro.r(R_OUTPUT_HELPERS_CODE)
            result['output_helpers_loaded'] = True
        except Exception as e:
            result['errors'].append(f"Failed to configure R console: {e}")
    
    result['success'] = len(result['errors']) == 0
    return result


class RMagicExecutionError(Exception):
    """Raised when a %%R cell encounters an R error.

    Ensures Jupyter's "Run All" stops on the first failing R cell,
    matching the behaviour of %%scala / %%java MagicExecutionError.
    """
    pass


def _r_df_to_pandas(r_obj):
    """Convert an R data.frame (or tibble/grouped_df) to a pandas DataFrame.

    Coerces to plain data.frame first so rpy2's pandas2ri converter
    always gets a standard R data.frame.  If direct conversion fails
    (e.g. columns contain S4 objects like DBI ``Id``), falls back to
    converting all columns to character vectors first.
    Returns ``None`` only if both strategies fail.
    """
    import rpy2.robjects as ro
    from rpy2.robjects import pandas2ri

    try:
        r_df = ro.r('as.data.frame')(r_obj)
        with (ro.default_converter + pandas2ri.converter).context():
            return ro.conversion.get_conversion().rpy2py(r_df)
    except Exception:
        pass

    # Fallback: coerce every column to character so S4/complex
    # objects become their text representation.
    try:
        r_df_chr = ro.r(
            'function(df) as.data.frame('
            'lapply(df, function(col) '
            'if (is.character(col) || is.numeric(col) || '
            'is.logical(col) || inherits(col, "Date") || '
            'inherits(col, "POSIXct")) col '
            'else vapply(col, function(x) '
            'paste(capture.output(print(x)), collapse=" "), "")),'
            'stringsAsFactors=FALSE)'
        )(r_obj)
        with (ro.default_converter + pandas2ri.converter).context():
            return ro.conversion.get_conversion().rpy2py(r_df_chr)
    except Exception as exc:
        print(f"Warning: R→pandas grid conversion failed ({exc}); "
              "falling back to text output.", file=sys.stderr)
        return None


def _lazy_tbl_to_pandas(ro):
    """Execute a lazy dbplyr table's SQL via Snowpark and return pandas.

    Reads the SQL string left in ``..__lazy_sql__`` by our R wrapping
    code, runs it through the active Snowpark session, and returns a
    pandas DataFrame.  Falls back to collecting in R + pandas2ri if
    Snowpark is unavailable (e.g. running outside Workspace).

    Context awareness: if the R connection's database/schema differs
    from the Snowpark session's, the Snowpark context is temporarily
    aligned before executing the SQL and restored afterwards.

    Returns ``None`` on failure.
    """
    try:
        sql = str(ro.r('..__lazy_sql__')[0])
        if not sql:
            return None
    except Exception:
        return None

    # Preferred: execute via Snowpark (no R→Python data copy)
    try:
        from snowflake.snowpark.context import get_active_session
        session = get_active_session()

        # Context alignment: the lazy tbl's SQL was generated against
        # the R connection's context; ensure Snowpark matches.
        r_db = r_sch = None
        try:
            r_db = str(ro.r('con@database')[0])
            r_sch = str(ro.r('con@schema')[0])
        except Exception:
            pass

        py_db, py_sch = _get_snowpark_context()
        need_restore = False

        if (r_db and py_db
                and (r_db != py_db or r_sch != py_sch)):
            session.sql(
                f'USE DATABASE "{r_db}"').collect()
            session.sql(
                f'USE SCHEMA "{r_sch}"').collect()
            need_restore = True

        try:
            return session.sql(sql).to_pandas()
        finally:
            if need_restore:
                try:
                    session.sql(
                        f'USE DATABASE "{py_db}"').collect()
                    session.sql(
                        f'USE SCHEMA "{py_sch}"').collect()
                except Exception:
                    pass
    except Exception:
        pass

    # Fallback: collect() in R, then convert via pandas2ri
    try:
        r_df = ro.r('as.data.frame(dplyr::collect(..__wv__$value))')
        return _r_df_to_pandas(r_df)
    except Exception as exc:
        print(f"Warning: lazy table grid display failed ({exc}); "
              "falling back to text output.", file=sys.stderr)
        return None


def _snowpark_df_to_sql(df):
    """Extract the SQL query from a Snowpark DataFrame.

    Returns the SQL string, or ``None`` if extraction fails.
    """
    try:
        from snowflake.snowpark import DataFrame as SparkDF
        if not isinstance(df, SparkDF):
            return None
    except ImportError:
        return None

    try:
        queries = df.queries
        if queries and queries.get('queries'):
            return queries['queries'][-1]
    except Exception:
        pass
    return None


def _get_snowpark_context():
    """Return (database, schema) from the active Snowpark session.

    Returns ``(None, None)`` if the session is unavailable.
    """
    try:
        from snowflake.snowpark.context import get_active_session
        s = get_active_session()
        return (
            (s.get_current_database() or '').strip('"'),
            (s.get_current_schema() or '').strip('"'),
        )
    except Exception:
        return (None, None)


def _resolve_var(name, local_ns, shell):
    """Resolve a Python variable by *name* from the notebook namespace.

    Checks ``local_ns`` first, then the IPython ``shell.user_ns``.
    Supports dotted paths (e.g. ``pkg.module.obj``).
    Returns the object or ``None``.
    """
    parts = name.split('.')
    obj = None
    ns_list = []
    if local_ns:
        ns_list.append(local_ns)
    if shell is not None and hasattr(shell, 'user_ns'):
        ns_list.append(shell.user_ns)
    for ns in ns_list:
        if parts[0] in ns:
            obj = ns[parts[0]]
            try:
                for attr in parts[1:]:
                    obj = getattr(obj, attr)
            except AttributeError:
                obj = None
                continue
            return obj
    return None


def _build_safe_r_magics_class():
    """Build and return an RMagics subclass with improved behaviour.

    Enhancements over stock rpy2 ``RMagics``:

    1. **Error propagation** — R errors always raise
       ``RMagicExecutionError`` so Jupyter "Run All" stops on failure.
    2. **Single-block output** — cell code is wrapped in R's
       ``capture.output({...})`` so ALL output (including visible
       return values like data.frames) is collected as a character
       vector.  ``writeLines()`` then sends it through a single
       ``write_console_regular`` call → our buffer → one ``print()``
       at the end.  ``flush()`` returns empty to rpy2 so it has
       nothing to publish via ``publish_display_data``.  This
       eliminates the extra line-breaks that Workspace Notebooks
       inject between separate display calls.  Same pattern as the
       ``%%scala`` / ``%%java`` magics.
    3. **Auto-detect grid display** — when the last visible expression
       is a ``data.frame`` (or tibble), it is converted to a pandas
       DataFrame and returned as the cell result so Workspace renders
       the interactive grid viewer.  Lazy ``dbplyr`` tables (``tbl_lazy``)
       are detected automatically: the generated SQL is extracted and
       executed directly via Snowpark, avoiding R→Python data copy.
       Use ``--text`` to opt out, or ``--df varname`` to explicitly
       pick which R variable to display.
    4. **Smart ``-i`` for Snowpark DataFrames** — when ``-i varname``
       references a Snowpark DataFrame, it is automatically injected
       into R as a lazy ``dbplyr`` table (via ``tbl(con, sql(...))``).
       Regular Python objects on the same ``-i`` are still passed
       through rpy2's native converter.  ``--snow pyvar[=r_name]`` is
       available as an explicit alternative.  Database/schema context
       is checked and aligned between the Snowpark and R sessions.
    5. **``--time``** — prints wall-clock execution time.
    6. **``--silent``** — suppresses all R text output.
    """
    import time
    import IPython.core.magic
    from rpy2.ipython.rmagic import RMagics, RInterpreterError

    @IPython.core.magic.magics_class
    class SafeRMagics(RMagics):

        def __init__(self, shell):
            super().__init__(shell)
            self._output_buf = []
            self._buffered_mode = False

        def write_console_regular(self, output):
            """Intercept R console output during buffered mode."""
            if self._buffered_mode:
                self._output_buf.append(output)
            else:
                super().write_console_regular(output)

        def flush(self):
            """In buffered mode, return empty to rpy2 so it has nothing
            to publish via ``publish_display_data``.  The real output
            stays in ``_output_buf`` and is emitted as a single
            ``print()`` at the end of our ``R()`` override.
            """
            if self._buffered_mode:
                parent_text = super().flush()
                if parent_text:
                    self._output_buf.append(parent_text)
                return ''
            return super().flush()

        @IPython.core.magic.needs_local_scope
        @IPython.core.magic.line_cell_magic
        @IPython.core.magic.no_var_expand
        def R(self, line, cell=None, local_ns=None):
            show_time = False
            silent = False
            text_only = False
            df_varname = None
            snow_inputs = []
            o_vars = []

            if cell is not None:
                parts = line.split()
                if "--time" in parts:
                    show_time = True
                    parts.remove("--time")
                if "--silent" in parts:
                    silent = True
                    parts.remove("--silent")
                if "--text" in parts:
                    text_only = True
                    parts.remove("--text")
                # --df varname — explicit variable to return as grid
                # (double-dash to avoid conflict with rpy2's -d/--display)
                df_idx = None
                for i, p in enumerate(parts):
                    if p == '--df' and i + 1 < len(parts):
                        df_idx = i
                        break
                if df_idx is not None:
                    df_varname = parts[df_idx + 1]
                    del parts[df_idx:df_idx + 2]

                # --snow pyvar[=r_name] — explicit Snowpark DF injection
                while True:
                    idx = None
                    for i, p in enumerate(parts):
                        if p == '--snow' and i + 1 < len(parts):
                            idx = i
                            break
                    if idx is None:
                        break
                    spec = parts[idx + 1]
                    if '=' in spec:
                        py_name, r_name = spec.split('=', 1)
                    else:
                        py_name = r_name = spec
                    snow_inputs.append((py_name, r_name))
                    del parts[idx:idx + 2]

                # --- Intercept -i / --input for Snowpark DataFrames ---
                # For each -i variable, check if it's a Snowpark DF.
                # Snowpark DFs are removed from -i and handled as lazy
                # dbplyr tbls; everything else stays on -i for rpy2.
                remaining_i_vars = []
                i_segments = []
                idx = 0
                while idx < len(parts):
                    if (parts[idx] in ('-i', '--input')
                            and idx + 1 < len(parts)):
                        i_segments.append((idx, idx + 2))
                        for vspec in parts[idx + 1].split(','):
                            vspec = vspec.strip()
                            if not vspec:
                                continue
                            lhs, sep, rhs = vspec.partition('=')
                            if not sep:
                                rhs = lhs
                            var = _resolve_var(
                                rhs, local_ns, self.shell)
                            if (var is not None
                                    and _snowpark_df_to_sql(var)
                                    is not None):
                                snow_inputs.append((rhs, lhs))
                            else:
                                remaining_i_vars.append(vspec)
                        idx += 2
                    else:
                        idx += 1
                for start, end in reversed(i_segments):
                    del parts[start:end]
                if remaining_i_vars:
                    parts.extend(['-i', ','.join(remaining_i_vars)])

                # Collect -o variable names so we can grid-display them
                idx = 0
                while idx < len(parts):
                    if (parts[idx] in ('-o', '--output')
                            and idx + 1 < len(parts)):
                        for vspec in parts[idx + 1].split(','):
                            vname = vspec.strip()
                            if vname:
                                o_vars.append(vname)
                        idx += 2
                    else:
                        idx += 1

                line = " ".join(parts)

            # Inject Snowpark DataFrames as lazy dbplyr tbls
            if cell is not None and snow_inputs:
                import rpy2.robjects as ro
                preamble = []

                # Capture Snowpark session context once
                py_db, py_sch = _get_snowpark_context()
                ctx_checked = False

                for py_name, r_name in snow_inputs:
                    var = _resolve_var(
                        py_name, local_ns, self.shell)
                    if var is None:
                        print(
                            f"Warning: Python variable '{py_name}' "
                            "not found; skipping Snowpark injection.",
                            file=sys.stderr)
                        continue
                    sql = _snowpark_df_to_sql(var)
                    if sql is None:
                        print(
                            f"Warning: '{py_name}' is not a Snowpark "
                            "DataFrame; skipping Snowpark injection.",
                            file=sys.stderr)
                        continue

                    # Context alignment (once per cell)
                    if not ctx_checked and py_db:
                        ctx_checked = True
                        preamble.append(
                            '..__py_db__ <- '
                            f'"{py_db}"\n'
                            '..__py_sch__ <- '
                            f'"{py_sch}"\n'
                            '..__r_ctx__ <- tryCatch(\n'
                            '  dbGetQuery(con, "SELECT '
                            'CURRENT_DATABASE(), '
                            'CURRENT_SCHEMA()"),\n'
                            '  error = function(e) NULL)\n'
                            'if (!is.null(..__r_ctx__)) {\n'
                            '  if (!identical(..__r_ctx__[1,1], '
                            '..__py_db__) ||\n'
                            '      !identical(..__r_ctx__[1,2], '
                            '..__py_sch__)) {\n'
                            '    cat(sprintf("Note: Aligning R '
                            'session context to Snowpark '
                            '(%s.%s)\\n",\n'
                            '        ..__py_db__, ..__py_sch__))\n'
                            '    dbExecute(con, sprintf(\''
                            'USE DATABASE "%s"\', '
                            '..__py_db__))\n'
                            '    dbExecute(con, sprintf(\''
                            'USE SCHEMA "%s"\', '
                            '..__py_sch__))\n'
                            '  }\n'
                            '}\n'
                            'rm(..__py_db__, ..__py_sch__, '
                            '..__r_ctx__)')

                    tmp = f'..__snow_sql_{r_name}__'
                    ro.r.assign(tmp, sql)
                    preamble.append(
                        f'{r_name} <- dplyr::tbl(con, '
                        f'dplyr::sql({tmp}))\n'
                        f'rm({tmp})')
                if preamble:
                    cell = '\n'.join(preamble) + '\n' + cell

            has_io_flags = cell is not None and any(
                f in line for f in ('-i ', '-o ', '-w ', '-h ',
                                    '-i\t', '-o\t', '-w\t', '-h\t')
            )
            has_plot_flags = cell is not None and any(
                f in line for f in ('-w ', '-h ', '-w\t', '-h\t')
            )

            # Determine grid mode:
            #  - "auto"  → withVisible wrapping; detect last df
            #  - "named" → capture.output wrapping; pull named var
            #  - "io"    → -i/-o present; wrap text but let rpy2 handle vars
            #  - None    → text-only (current behaviour)
            grid_mode = None
            if cell is not None and not has_io_flags:
                if df_varname:
                    grid_mode = "named"
                elif not text_only:
                    grid_mode = "auto"
            elif cell is not None and has_io_flags and not has_plot_flags:
                if not text_only:
                    grid_mode = "io"

            if cell is not None and not has_io_flags:
                if grid_mode == "auto":
                    # withVisible captures the last expression's value
                    # + visibility WITHOUT auto-printing it, while the
                    # outer {…} (not local()) keeps variable assignments
                    # in the calling scope.  capture.output collects
                    # only intermediate cat()/print() text.
                    #
                    # After evaluating, we check three cases:
                    #  a) data.frame/tibble → grid via pandas2ri
                    #  b) lazy dbplyr table → extract SQL, run in Snowpark
                    #  c) anything else     → text output
                    cell = (
                        '..__co__ <- capture.output({\n'
                        '  ..__wv__ <- withVisible({\n'
                        + cell
                        + '\n  })\n'
                        '})\n'
                        '..__is_grid__ <- ..__wv__$visible && '
                        'is.data.frame(..__wv__$value)\n'
                        '..__is_lazy__ <- ..__wv__$visible && '
                        '!..__is_grid__ && '
                        'inherits(..__wv__$value, "tbl_lazy")\n'
                        '..__lazy_sql__ <- NULL\n'
                        'if (..__is_lazy__ && '
                        'requireNamespace("dbplyr", quietly = TRUE)) {\n'
                        '  ..__lazy_sql__ <- as.character('
                        'dbplyr::remote_query(..__wv__$value))\n'
                        '}\n'
                        'if (..__wv__$visible && '
                        '!..__is_grid__ && !..__is_lazy__) {\n'
                        '  ..__co__ <- c(..__co__, '
                        'capture.output(print(..__wv__$value)))\n'
                        '}\n'
                        'if (length(..__co__) > 0L) writeLines(..__co__)'
                    )
                else:
                    # text-only or named-df: standard wrapping
                    cell = (
                        '..__co__ <- capture.output({\n'
                        + cell
                        + '\n})\n'
                        'if (length(..__co__) > 0L) writeLines(..__co__)\n'
                        'rm(..__co__)'
                    )

            # Wrap -i/-o cells in capture.output for clean text output.
            # Variables stay in global scope so rpy2's -o can find them.
            if cell is not None and grid_mode == "io":
                cell = (
                    '..__co__ <- capture.output({\n'
                    + cell
                    + '\n})\n'
                    'if (length(..__co__) > 0L) writeLines(..__co__)\n'
                    'rm(..__co__)'
                )

            self._output_buf.clear()
            self._buffered_mode = True
            t0 = time.time()

            try:
                result = super().R(line, cell=cell, local_ns=local_ns)

                remaining = ''.join(self._output_buf)
                if remaining and not silent:
                    print(remaining, end='')

                if show_time:
                    elapsed = time.time() - t0
                    print(f"[R executed in {elapsed:.2f}s]")

                # --- Grid display post-processing ---
                pdf = None
                if grid_mode == "auto":
                    import rpy2.robjects as ro
                    try:
                        is_grid = bool(ro.r('..__is_grid__')[0])
                        is_lazy = bool(ro.r('..__is_lazy__')[0])

                        if is_grid:
                            pdf = _r_df_to_pandas(
                                ro.r('..__wv__$value'))
                        elif is_lazy:
                            pdf = _lazy_tbl_to_pandas(ro)
                            if pdf is None:
                                # Grid conversion failed; print text repr
                                try:
                                    txt = ro.r(
                                        'capture.output('
                                        'print(..__wv__$value))')
                                    print('\n'.join(
                                        str(x) for x in txt))
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    finally:
                        try:
                            ro.r('rm(..__co__, ..__wv__, '
                                 '..__is_grid__, ..__is_lazy__, '
                                 '..__lazy_sql__)')
                        except Exception:
                            pass
                elif grid_mode == "named":
                    import rpy2.robjects as ro
                    try:
                        r_obj = ro.globalenv[df_varname]
                        is_df = bool(ro.r(
                            f'is.data.frame({df_varname})')[0])
                        if is_df:
                            pdf = _r_df_to_pandas(r_obj)
                        else:
                            print(f"Warning: R variable '{df_varname}'"
                                  " is not a data.frame; "
                                  "skipping grid display.",
                                  file=sys.stderr)
                    except Exception as exc:
                        print(f"Warning: could not read R variable "
                              f"'{df_varname}': {exc}",
                              file=sys.stderr)

                # --- Grid display for -o output variables ---
                # rpy2 pushes -o variables into the notebook namespace
                # via pandas2ri.  If the last -o var is a pandas DF,
                # return it so Workspace renders the grid viewer.
                if pdf is None and o_vars and not text_only:
                    import pandas as pd
                    ns = {}
                    if local_ns:
                        ns.update(local_ns)
                    if (self.shell is not None
                            and hasattr(self.shell, 'user_ns')):
                        ns.update(self.shell.user_ns)
                    for vname in reversed(o_vars):
                        obj = ns.get(vname)
                        if isinstance(obj, pd.DataFrame):
                            pdf = obj
                            break

                if pdf is not None:
                    return pdf
                return result

            except RInterpreterError as e:
                remaining = ''.join(self._output_buf)
                if remaining:
                    print(remaining, end='')

                if show_time:
                    elapsed = time.time() - t0
                    print(f"[R failed after {elapsed:.2f}s]")

                msg = str(e.err) if hasattr(e, 'err') and e.err else str(e)
                print(msg, file=sys.stderr)
                raise RMagicExecutionError(msg) from e

            finally:
                self._buffered_mode = False
                self._output_buf.clear()

    # rpy2's R() uses @magic_arguments()/@argument() decorators that attach
    # a .parser attribute.  Our override loses that metadata, causing
    # parse_argstring(self.R, line) to fail with
    #   AttributeError: 'function' object has no attribute 'parser'
    # Copy it from the parent so the argparse machinery works.
    SafeRMagics.R.parser = RMagics.R.parser

    return SafeRMagics

# R output helpers code - can be loaded independently of connection management
#
# NOTE: With the SafeRMagics buffered output (above), R's normal print()/cat()
# should work cleanly in most cases.  These helpers are still loaded for
# backward compatibility and for edge cases where explicit formatting control
# is needed (e.g. rview with row limits, rglimpse for structure).
R_OUTPUT_HELPERS_CODE = '''
# =============================================================================
# Output Helpers for Workspace Notebooks
# =============================================================================
# These helpers provide explicit formatting control.
# With the buffered %%R magic, normal print()/cat() should also work cleanly.

#' Print an object cleanly (workaround for Workspace Notebook rendering)
#' 
#' @param x Object to print
#' @param ... Additional arguments passed to print()
#' @examples
#' rprint(mtcars)
#' rprint(head(iris, 10))
rprint <- function(x, ...) {
  writeLines(capture.output(print(x, ...)))
  invisible(x)
}

#' View a data frame cleanly
#' 
#' @param df Data frame to display
#' @param n Number of rows to show (default: all)
#' @examples
#' rview(mtcars)
#' rview(iris, n = 10)
rview <- function(df, n = NULL) {
  if (!is.null(n)) {
    df <- head(df, n)
  }
  writeLines(capture.output(print(df)))
  invisible(df)
}

#' Print a tibble/data frame with glimpse, cleanly formatted
#' 
#' @param df Data frame to glimpse
rglimpse <- function(df) {
  if (requireNamespace("dplyr", quietly = TRUE)) {
    writeLines(capture.output(dplyr::glimpse(df)))
  } else {
    writeLines(capture.output(str(df)))
  }
  invisible(df)
}

#' Clean replacement for cat() in Workspace Notebooks
#' 
#' Builds the entire string first, then outputs with writeLines()
#' to avoid extra line breaks that Workspace Notebooks adds.
#' 
#' @param ... Arguments to concatenate (like cat())
#' @param sep Separator between arguments (default: "")
#' @examples
#' rcat("Value:", 42)
#' rcat("Name: ", name, ", Age: ", age)
rcat <- function(..., sep = "") {
  args <- list(...)
  output <- paste(sapply(args, as.character), collapse = sep)
  writeLines(output)
  invisible(NULL)
}

#' Print multiple lines cleanly
#' 
#' @param ... Lines to print (each argument is a separate line)
#' @examples
#' rlines("Line 1", "Line 2", "Line 3")
rlines <- function(...) {
  lines <- c(...)
  writeLines(paste(lines, collapse = "\\n"))
  invisible(NULL)
}

message("Output helpers loaded (use these for cleaner formatting):")
message("  - rprint(x)      : Print any object cleanly")
message("  - rview(df, n)   : View data frame (optional row limit)")
message("  - rglimpse(df)   : Glimpse data frame structure")
message("  - rcat(...)      : Clean replacement for cat()")
message("  - rlines(...)    : Print multiple lines cleanly")
'''


def init_r_output_helpers() -> Tuple[bool, str]:
    """
    Load R output helper functions for cleaner display in Workspace Notebooks.
    
    Workspace Notebooks add extra line breaks to R output. These helpers
    use writeLines(capture.output(...)) to produce cleaner formatting.
    
    Functions loaded:
    - rprint(x): Print any object cleanly
    - rview(df, n): View data frame with optional row limit
    - rglimpse(df): Glimpse data frame structure
    
    Returns:
        Tuple of (success, message)
    
    Example:
        >>> init_r_output_helpers()
        >>> # Then in R: rprint(mtcars), rview(iris, n=10)
    """
    try:
        import rpy2.robjects as ro
        ro.r(R_OUTPUT_HELPERS_CODE)
        return True, "R output helpers loaded"
    except Exception as e:
        return False, f"Failed to load R output helpers: {e}"


def set_r_console_width(width: int = 120, tibble_width: Optional[int] = None) -> None:
    """
    Set R console width for better output formatting in notebooks.
    
    Args:
        width: Console width in characters (default: 120)
        tibble_width: Width for tibble printing (default: Inf for all columns)
    
    Example:
        >>> set_r_console_width(150)  # Wider output
        >>> set_r_console_width(80)   # Narrower output
    """
    import rpy2.robjects as ro
    ro.r(f'options(width = {width})')
    
    if tibble_width is None:
        ro.r('options(tibble.width = Inf)')
        ro.r('options(pillar.width = Inf)')
    else:
        ro.r(f'options(tibble.width = {tibble_width})')
        ro.r(f'options(pillar.width = {tibble_width})')


# =============================================================================
# PAT Management
# =============================================================================

class PATManager:
    """
    Manager for Programmatic Access Tokens (PAT) for ADBC authentication.
    
    Example:
        >>> from r_helpers import PATManager
        >>> pat_mgr = PATManager(session)
        >>> pat_mgr.create_pat(days_to_expiry=1)
        >>> if pat_mgr.is_valid():
        ...     print("PAT is valid")
    """
    
    def __init__(self, session, token_name: str = PAT_TOKEN_NAME):
        """
        Initialize PAT manager.
        
        Args:
            session: Snowpark session
            token_name: Name for the PAT (default: 'r_adbc_pat')
        """
        self.session = session
        self.token_name = token_name
        self._token_secret: Optional[str] = None
        self._created_at: Optional[datetime] = None
        self._expires_at: Optional[datetime] = None
        self._role_restriction: Optional[str] = None
        self._user: Optional[str] = None
    
    @property
    def token(self) -> Optional[str]:
        """Get the current PAT token (if created)."""
        return self._token_secret
    
    @property
    def is_expired(self) -> bool:
        """Check if the PAT has expired."""
        if self._expires_at is None:
            return True
        return datetime.now() > self._expires_at
    
    def is_valid(self) -> bool:
        """Check if PAT exists and is not expired."""
        return self._token_secret is not None and not self.is_expired
    
    def time_remaining(self) -> Optional[timedelta]:
        """Get time remaining until PAT expires."""
        if self._expires_at is None:
            return None
        remaining = self._expires_at - datetime.now()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)
    
    def create_pat(
        self,
        days_to_expiry: int = 1,
        role_restriction: Optional[str] = None,
        network_policy_bypass_mins: int = 240,
        force_recreate: bool = False
    ) -> Dict[str, Any]:
        """
        Create a new Programmatic Access Token.
        
        Args:
            days_to_expiry: Token validity in days (default: 1)
            role_restriction: Role to restrict token to (default: current role)
            network_policy_bypass_mins: Minutes to bypass network policy (default: 240)
            force_recreate: Remove existing PAT first (default: False)
        
        Returns:
            Dict with creation status and token info
        """
        result = {
            'success': False,
            'user': None,
            'role_restriction': None,
            'expires_at': None,
            'error': None
        }
        
        try:
            # Get current user
            self._user = self.session.sql('SELECT CURRENT_USER()').collect()[0][0]
            result['user'] = self._user
            
            # Get role restriction
            if role_restriction is None:
                role_restriction = self.session.get_current_role().replace('"', '')
            self._role_restriction = role_restriction
            result['role_restriction'] = role_restriction
            
            # Remove existing PAT if requested or if it exists
            if force_recreate:
                self.remove_pat()
            
            # Create new PAT
            pat_result = self.session.sql(f'''
                ALTER USER {self._user}
                ADD PROGRAMMATIC ACCESS TOKEN {self.token_name}
                  ROLE_RESTRICTION = '{role_restriction}'
                  DAYS_TO_EXPIRY = {days_to_expiry}
                  MINS_TO_BYPASS_NETWORK_POLICY_REQUIREMENT = {network_policy_bypass_mins}
                  COMMENT = 'PAT for R/ADBC from Workspace Notebook'
            ''').collect()
            
            # Store token
            self._token_secret = pat_result[0]['token_secret']
            self._created_at = datetime.now()
            self._expires_at = self._created_at + timedelta(days=days_to_expiry)
            
            # Set environment variable
            os.environ["SNOWFLAKE_PAT"] = self._token_secret
            
            result['success'] = True
            result['expires_at'] = self._expires_at.isoformat()
            
        except Exception as e:
            error_msg = str(e)
            # Check for common errors
            if "already exists" in error_msg.lower():
                result['error'] = (
                    f"PAT '{self.token_name}' already exists. "
                    "Use force_recreate=True to replace it."
                )
            else:
                result['error'] = error_msg
        
        return result
    
    def remove_pat(self) -> bool:
        """Remove the PAT from the user."""
        try:
            if self._user is None:
                self._user = self.session.sql('SELECT CURRENT_USER()').collect()[0][0]
            
            self.session.sql(f'''
                ALTER USER {self._user} 
                REMOVE PROGRAMMATIC ACCESS TOKEN {self.token_name}
            ''').collect()
            
            # Clear local state
            self._token_secret = None
            self._created_at = None
            self._expires_at = None
            
            # Clear environment variable
            if "SNOWFLAKE_PAT" in os.environ:
                del os.environ["SNOWFLAKE_PAT"]
            
            return True
        except Exception:
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get current PAT status."""
        remaining = self.time_remaining()
        return {
            'exists': self._token_secret is not None,
            'is_valid': self.is_valid(),
            'user': self._user,
            'role_restriction': self._role_restriction,
            'created_at': self._created_at.isoformat() if self._created_at else None,
            'expires_at': self._expires_at.isoformat() if self._expires_at else None,
            'time_remaining': str(remaining) if remaining else None,
            'env_var_set': 'SNOWFLAKE_PAT' in os.environ
        }
    
    def refresh_if_needed(self, min_remaining_hours: float = 1.0) -> Dict[str, Any]:
        """
        Refresh PAT if it will expire soon.
        
        Args:
            min_remaining_hours: Refresh if less than this many hours remain
        
        Returns:
            Dict with refresh status
        """
        result = {'refreshed': False, 'reason': None}
        
        remaining = self.time_remaining()
        if remaining is None:
            result['reason'] = 'No existing PAT'
            self.create_pat()
            result['refreshed'] = True
        elif remaining.total_seconds() < min_remaining_hours * 3600:
            result['reason'] = f'Less than {min_remaining_hours}h remaining'
            self.create_pat(force_recreate=True)
            result['refreshed'] = True
        else:
            result['reason'] = f'{remaining} remaining, no refresh needed'
        
        return result


# =============================================================================
# Alternative Authentication Methods
# =============================================================================

class KeyPairAuth:
    """
    Helper for Key Pair (JWT) authentication with Snowflake ADBC.
    
    Key pair authentication uses RSA keys instead of passwords/tokens.
    The private key must be registered with the Snowflake user.
    
    ADBC auth_type: 'auth_jwt'
    
    Example:
        >>> from r_helpers import KeyPairAuth
        >>> kp_auth = KeyPairAuth()
        >>> kp_auth.generate_key_pair()  # Or load existing
        >>> kp_auth.configure_for_adbc()
    """
    
    def __init__(self):
        self._private_key_path: Optional[str] = None
        self._public_key_path: Optional[str] = None
        self._private_key_pem: Optional[str] = None
        self._passphrase: Optional[str] = None
    
    def generate_key_pair(
        self,
        key_size: int = 2048,
        output_dir: str = "/tmp",
        passphrase: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate a new RSA key pair for Snowflake authentication.
        
        Args:
            key_size: RSA key size in bits (default: 2048)
            output_dir: Directory to save keys
            passphrase: Optional passphrase to encrypt private key
        
        Returns:
            Dict with key paths and public key for registration
        """
        result = {
            'success': False,
            'private_key_path': None,
            'public_key_path': None,
            'public_key_for_snowflake': None,
            'error': None
        }
        
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
            
            # Generate private key
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size,
                backend=default_backend()
            )
            
            # Determine encryption
            if passphrase:
                encryption = serialization.BestAvailableEncryption(passphrase.encode())
                self._passphrase = passphrase
            else:
                encryption = serialization.NoEncryption()
            
            # Serialize private key
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=encryption
            )
            
            # Serialize public key
            public_key = private_key.public_key()
            public_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            
            # Save keys
            private_path = f"{output_dir}/snowflake_rsa_key.p8"
            public_path = f"{output_dir}/snowflake_rsa_key.pub"
            
            with open(private_path, 'wb') as f:
                f.write(private_pem)
            os.chmod(private_path, 0o600)  # Restrict permissions
            
            with open(public_path, 'wb') as f:
                f.write(public_pem)
            
            self._private_key_path = private_path
            self._public_key_path = public_path
            self._private_key_pem = private_pem.decode()
            
            # Format public key for Snowflake (remove headers/footers, join lines)
            public_key_lines = public_pem.decode().strip().split('\n')
            public_key_for_sf = ''.join(public_key_lines[1:-1])
            
            result['success'] = True
            result['private_key_path'] = private_path
            result['public_key_path'] = public_path
            result['public_key_for_snowflake'] = public_key_for_sf
            
        except ImportError:
            result['error'] = "cryptography package not installed. Run: pip install cryptography"
        except Exception as e:
            result['error'] = str(e)
        
        return result
    
    def load_private_key(
        self,
        key_path: str,
        passphrase: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Load an existing private key file.
        
        Args:
            key_path: Path to private key file (.p8 or .pem)
            passphrase: Passphrase if key is encrypted
        
        Returns:
            Dict with load status
        """
        result = {'success': False, 'error': None}
        
        try:
            with open(key_path, 'rb') as f:
                private_key_data = f.read()
            
            # Verify it's a valid key
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            
            password = passphrase.encode() if passphrase else None
            serialization.load_pem_private_key(
                private_key_data,
                password=password,
                backend=default_backend()
            )
            
            self._private_key_path = key_path
            self._private_key_pem = private_key_data.decode()
            self._passphrase = passphrase
            result['success'] = True
            
        except FileNotFoundError:
            result['error'] = f"Key file not found: {key_path}"
        except Exception as e:
            result['error'] = f"Failed to load key: {e}"
        
        return result
    
    def register_public_key_sql(self, public_key: str, user: Optional[str] = None) -> str:
        """
        Generate SQL to register public key with Snowflake user.
        
        Args:
            public_key: Public key string (from generate_key_pair)
            user: Username (default: current user from env)
        
        Returns:
            SQL statement to execute
        """
        user = user or os.environ.get('SNOWFLAKE_USER', 'CURRENT_USER()')
        return f"ALTER USER {user} SET RSA_PUBLIC_KEY = '{public_key}';"
    
    def configure_for_adbc(self) -> Dict[str, Any]:
        """
        Configure environment variables for ADBC key pair auth.
        
        Returns:
            Dict with configuration status
        """
        result = {'success': False, 'auth_type': 'auth_jwt', 'error': None}
        
        if not self._private_key_path:
            result['error'] = "No private key loaded. Call generate_key_pair() or load_private_key() first."
            return result
        
        # Set environment variables for ADBC
        os.environ['SNOWFLAKE_AUTH_TYPE'] = 'auth_jwt'
        os.environ['SNOWFLAKE_PRIVATE_KEY_PATH'] = self._private_key_path
        if self._passphrase:
            os.environ['SNOWFLAKE_PRIVATE_KEY_PASSPHRASE'] = self._passphrase
        
        result['success'] = True
        result['private_key_path'] = self._private_key_path
        result['has_passphrase'] = self._passphrase is not None
        
        return result
    
    def get_status(self) -> Dict[str, Any]:
        """Get current key pair auth configuration status."""
        return {
            'private_key_loaded': self._private_key_path is not None,
            'private_key_path': self._private_key_path,
            'has_passphrase': self._passphrase is not None,
            'env_auth_type': os.environ.get('SNOWFLAKE_AUTH_TYPE'),
            'env_key_path': os.environ.get('SNOWFLAKE_PRIVATE_KEY_PATH')
        }


class OAuthAuth:
    """
    Helper for OAuth authentication with Snowflake ADBC.
    
    OAuth authentication requires a security integration configured
    in Snowflake with an external identity provider.
    
    ADBC auth_type: 'auth_oauth'
    
    Example:
        >>> from r_helpers import OAuthAuth
        >>> oauth = OAuthAuth()
        >>> oauth.set_token(access_token)
        >>> oauth.configure_for_adbc()
    """
    
    def __init__(self):
        self._access_token: Optional[str] = None
        self._token_type: str = "Bearer"
    
    def set_token(self, access_token: str, token_type: str = "Bearer") -> None:
        """
        Set the OAuth access token.
        
        Args:
            access_token: OAuth access token from IdP
            token_type: Token type (default: Bearer)
        """
        self._access_token = access_token
        self._token_type = token_type
    
    def load_spcs_token(self) -> Dict[str, Any]:
        """
        Attempt to load the SPCS OAuth token (for testing - known to not work).
        
        Returns:
            Dict with token load status
        """
        result = {'success': False, 'error': None, 'warning': None}
        
        token_path = '/snowflake/session/token'
        
        if os.path.exists(token_path):
            try:
                with open(token_path, 'r') as f:
                    self._access_token = f.read().strip()
                result['success'] = True
                result['warning'] = (
                    "SPCS token loaded, but this is known to NOT work for ADBC. "
                    "The token is restricted to specific Snowflake connectors."
                )
            except Exception as e:
                result['error'] = f"Failed to read token: {e}"
        else:
            result['error'] = f"Token file not found: {token_path}"
        
        return result
    
    def configure_for_adbc(self) -> Dict[str, Any]:
        """
        Configure environment variables for ADBC OAuth auth.
        
        Returns:
            Dict with configuration status
        """
        result = {'success': False, 'auth_type': 'auth_oauth', 'error': None}
        
        if not self._access_token:
            result['error'] = "No access token set. Call set_token() first."
            return result
        
        os.environ['SNOWFLAKE_AUTH_TYPE'] = 'auth_oauth'
        os.environ['SNOWFLAKE_OAUTH_TOKEN'] = self._access_token
        
        result['success'] = True
        result['token_length'] = len(self._access_token)
        
        return result
    
    def get_status(self) -> Dict[str, Any]:
        """Get current OAuth auth configuration status."""
        return {
            'token_set': self._access_token is not None,
            'token_length': len(self._access_token) if self._access_token else 0,
            'token_type': self._token_type,
            'env_auth_type': os.environ.get('SNOWFLAKE_AUTH_TYPE'),
            'env_token_set': 'SNOWFLAKE_OAUTH_TOKEN' in os.environ
        }


# R code for alternative authentication connections
R_ALT_AUTH_CODE = '''
# =============================================================================
# Alternative Authentication Methods for ADBC
# =============================================================================

#' Test Key Pair (JWT) authentication
#' 
#' @param private_key_path Path to private key file (PKCS#8 .p8 format)
#' @param passphrase Optional passphrase for encrypted key
#' @return Connection object or error
#' 
#' Note: ADBC has two options for JWT auth:
#'   - jwt_private_key: path to PKCS#1 format file ("RSA PRIVATE KEY")
#'   - jwt_private_key_pkcs8_value: PKCS#8 key CONTENT (not path)
#' 
#' Since we generate PKCS#8 keys, we read the file and pass content directly.
test_keypair_auth <- function(private_key_path = NULL, passphrase = NULL) {
  account     <- Sys.getenv("SNOWFLAKE_ACCOUNT")
  user        <- Sys.getenv("SNOWFLAKE_USER")
  database    <- Sys.getenv("SNOWFLAKE_DATABASE")
  schema      <- Sys.getenv("SNOWFLAKE_SCHEMA")
  warehouse   <- Sys.getenv("SNOWFLAKE_WAREHOUSE")
  role        <- Sys.getenv("SNOWFLAKE_ROLE")
  public_host <- Sys.getenv("SNOWFLAKE_PUBLIC_HOST")
  
  # Get private key path from env if not provided
  if (is.null(private_key_path)) {
    private_key_path <- Sys.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
  }
  if (is.null(passphrase)) {
    passphrase <- Sys.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", unset = NA)
    if (is.na(passphrase)) passphrase <- NULL
  }
  
  if (identical(private_key_path, "")) {
    stop("Private key path not set. Set SNOWFLAKE_PRIVATE_KEY_PATH or pass as argument.")
  }
  
  # Read the private key content
  if (!file.exists(private_key_path)) {
    stop("Private key file not found: ", private_key_path)
  }
  
  private_key_content <- paste(readLines(private_key_path, warn = FALSE), collapse = "\\n")
  
  message("Testing Key Pair (JWT) authentication...")
  message("  Account: ", account)
  message("  User: ", user)
  message("  Key path: ", private_key_path)
  message("  Key format: ", ifelse(grepl("BEGIN PRIVATE KEY", private_key_content), "PKCS#8", "PKCS#1"))
  
  tryCatch({
    # Build connection arguments
    # For PKCS#8 keys, use jwt_private_key_pkcs8_value (content, not path)
    args <- list(
      adbcsnowflake::adbcsnowflake(),
      username                          = user,
      `adbc.snowflake.sql.account`      = account,
      `adbc.snowflake.sql.uri.host`     = public_host,
      `adbc.snowflake.sql.db`           = database,
      `adbc.snowflake.sql.schema`       = schema,
      `adbc.snowflake.sql.warehouse`    = warehouse,
      `adbc.snowflake.sql.role`         = role,
      `adbc.snowflake.sql.auth_type`    = "auth_jwt",
      `adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_value` = private_key_content
    )
    
    # Add passphrase if provided (for encrypted keys)
    if (!is.null(passphrase)) {
      args[["adbc.snowflake.sql.client_option.jwt_private_key_pkcs8_password"]] <- passphrase
    }
    
    db <- do.call(adbc_database_init, args)
    con <- adbc_connection_init(db)
    
    # Test query - collect to data frame to release the stream
    result <- con |> read_adbc("SELECT CURRENT_USER() AS USER, 'KEY_PAIR' AS AUTH_METHOD") |> as.data.frame()
    message("[OK] Key Pair authentication SUCCESSFUL")
    rprint(result)
    
    # Clean up test connection
    adbc_connection_release(con)
    adbc_database_release(db)
    
    return(list(success = TRUE, method = "auth_jwt"))
  }, error = function(e) {
    message("[FAIL] Key Pair authentication FAILED: ", e$message)
    return(list(success = FALSE, method = "auth_jwt", error = e$message))
  })
}

#' Test OAuth authentication
#' 
#' @param token OAuth access token
#' @return Connection object or error
test_oauth_auth <- function(token = NULL) {
  account     <- Sys.getenv("SNOWFLAKE_ACCOUNT")
  user        <- Sys.getenv("SNOWFLAKE_USER")
  database    <- Sys.getenv("SNOWFLAKE_DATABASE")
  schema      <- Sys.getenv("SNOWFLAKE_SCHEMA")
  warehouse   <- Sys.getenv("SNOWFLAKE_WAREHOUSE")
  role        <- Sys.getenv("SNOWFLAKE_ROLE")
  
  # Prefer internal SPCS host; fall back to public endpoint
  spcs_host <- Sys.getenv("SNOWFLAKE_HOST")
  if (nzchar(spcs_host)) {
    host <- spcs_host
  } else {
    host <- Sys.getenv("SNOWFLAKE_PUBLIC_HOST")
  }
  
  if (is.null(token)) {
    # In Workspace: read SPCS token; outside: use SNOWFLAKE_OAUTH_TOKEN
    if (nzchar(spcs_host) && file.exists("/snowflake/session/token")) {
      token <- trimws(readLines("/snowflake/session/token", warn = FALSE))
    } else {
      token <- Sys.getenv("SNOWFLAKE_OAUTH_TOKEN")
    }
  }
  
  if (identical(token, "")) {
    stop("OAuth token not available. Set SNOWFLAKE_OAUTH_TOKEN or pass as argument.")
  }
  
  message("Testing OAuth authentication...")
  message("  Account: ", account)
  message("  Host: ", host)
  message("  Token length: ", nchar(token))
  
  tryCatch({
    db <- adbc_database_init(
      adbcsnowflake::adbcsnowflake(),
      username                          = user,
      `adbc.snowflake.sql.account`      = account,
      `adbc.snowflake.sql.uri.host`     = host,
      `adbc.snowflake.sql.db`           = database,
      `adbc.snowflake.sql.schema`       = schema,
      `adbc.snowflake.sql.warehouse`    = warehouse,
      `adbc.snowflake.sql.role`         = role,
      `adbc.snowflake.sql.auth_type`    = "auth_oauth",
      `adbc.snowflake.sql.client_option.auth_token` = token
    )
    con <- adbc_connection_init(db)
    
    result <- con |> read_adbc("SELECT CURRENT_USER() AS USER, 'OAUTH' AS AUTH_METHOD") |> as.data.frame()
    message("[OK] OAuth authentication SUCCESSFUL")
    rprint(result)
    
    adbc_connection_release(con)
    adbc_database_release(db)
    
    return(list(success = TRUE, method = "auth_oauth"))
  }, error = function(e) {
    message("[FAIL] OAuth authentication FAILED: ", e$message)
    return(list(success = FALSE, method = "auth_oauth", error = e$message))
  })
}

#' Test SPCS OAuth token (expected to fail)
#' 
#' @return Connection result
test_spcs_token <- function() {
  token_path <- "/snowflake/session/token"
  
  if (!file.exists(token_path)) {
    message("[FAIL] SPCS token file not found: ", token_path)
    return(list(success = FALSE, method = "spcs_oauth", error = "Token file not found"))
  }
  
  token <- readLines(token_path, warn = FALSE)
  token <- paste(token, collapse = "")
  
  message("SPCS token loaded (", nchar(token), " chars)")
  message("Note: This is expected to FAIL - SPCS tokens are restricted to specific connectors")
  
  test_oauth_auth(token)
}

#' Test username/password authentication (expected to fail in SPCS)
#' 
#' @param password Password (if not using env var)
#' @return Connection result
test_password_auth <- function(password = NULL) {
  account     <- Sys.getenv("SNOWFLAKE_ACCOUNT")
  user        <- Sys.getenv("SNOWFLAKE_USER")
  database    <- Sys.getenv("SNOWFLAKE_DATABASE")
  schema      <- Sys.getenv("SNOWFLAKE_SCHEMA")
  warehouse   <- Sys.getenv("SNOWFLAKE_WAREHOUSE")
  role        <- Sys.getenv("SNOWFLAKE_ROLE")
  public_host <- Sys.getenv("SNOWFLAKE_PUBLIC_HOST")
  
  if (is.null(password)) {
    password <- Sys.getenv("SNOWFLAKE_PASSWORD")
  }
  
  if (identical(password, "")) {
    stop("Password not set. Set SNOWFLAKE_PASSWORD or pass as argument.")
  }
  
  message("Testing Username/Password authentication...")
  message("  Account: ", account)
  message("  User: ", user)
  message("Note: This is expected to FAIL in SPCS - OAuth is enforced")
  
  tryCatch({
    db <- adbc_database_init(
      adbcsnowflake::adbcsnowflake(),
      username                          = user,
      password                          = password,
      `adbc.snowflake.sql.account`      = account,
      `adbc.snowflake.sql.uri.host`     = public_host,
      `adbc.snowflake.sql.db`           = database,
      `adbc.snowflake.sql.schema`       = schema,
      `adbc.snowflake.sql.warehouse`    = warehouse,
      `adbc.snowflake.sql.role`         = role,
      `adbc.snowflake.sql.auth_type`    = "auth_snowflake"
    )
    con <- adbc_connection_init(db)
    
    result <- con |> read_adbc("SELECT CURRENT_USER() AS USER, 'PASSWORD' AS AUTH_METHOD") |> as.data.frame()
    message("[OK] Password authentication SUCCESSFUL (unexpected!)")
    rprint(result)
    
    adbc_connection_release(con)
    adbc_database_release(db)
    
    return(list(success = TRUE, method = "auth_snowflake"))
  }, error = function(e) {
    message("[FAIL] Password authentication FAILED (expected): ", e$message)
    return(list(success = FALSE, method = "auth_snowflake", error = e$message))
  })
}

message("Alternative auth test functions loaded:")
message("  - test_keypair_auth()  : Test JWT/Key Pair authentication")
message("  - test_oauth_auth()    : Test OAuth with external token")
message("  - test_spcs_token()    : Test SPCS OAuth token (expected to fail)")
message("  - test_password_auth() : Test username/password (expected to fail)")
'''


def init_r_alt_auth() -> Tuple[bool, str]:
    """
    Load R functions for testing alternative authentication methods.
    
    Functions loaded:
    - test_keypair_auth(): Test JWT/Key Pair authentication
    - test_oauth_auth(): Test OAuth with external token
    - test_spcs_token(): Test SPCS token (expected to fail)
    - test_password_auth(): Test password auth (expected to fail)
    
    Returns:
        Tuple of (success, message)
    """
    try:
        import rpy2.robjects as ro
        ro.r(R_ALT_AUTH_CODE)
        return True, "Alternative auth test functions loaded"
    except Exception as e:
        return False, f"Failed to load alt auth functions: {e}"


def create_pat(
    session,
    days_to_expiry: int = 1,
    role_restriction: Optional[str] = None,
    force_recreate: bool = True
) -> Tuple[bool, str]:
    """
    Convenience function to create a PAT.
    
    Args:
        session: Snowpark session
        days_to_expiry: Token validity in days
        role_restriction: Role to restrict token to
        force_recreate: Remove existing PAT first
    
    Returns:
        Tuple of (success, message)
    
    Example:
        >>> success, msg = create_pat(session, days_to_expiry=1)
        >>> print(msg)
    """
    mgr = PATManager(session)
    result = mgr.create_pat(
        days_to_expiry=days_to_expiry,
        role_restriction=role_restriction,
        force_recreate=force_recreate
    )
    
    if result['success']:
        msg = (
            f"PAT created successfully\n"
            f"  User: {result['user']}\n"
            f"  Role: {result['role_restriction']}\n"
            f"  Expires: {result['expires_at']}"
        )
        return True, msg
    else:
        return False, f"PAT creation failed: {result['error']}"


# =============================================================================
# Diagnostics
# =============================================================================

def check_environment() -> Dict[str, Any]:
    """
    Run comprehensive environment diagnostics.
    
    Returns:
        Dict with diagnostic results for each component
    
    Example:
        >>> diag = check_environment()
        >>> for component, status in diag.items():
        ...     print(f"{component}: {'[OK]' if status['ok'] else '[FAIL]'}")
    """
    diagnostics = {}
    
    # 1. Check R environment
    diagnostics['r_environment'] = _check_r_environment()
    
    # 2. Check rpy2
    diagnostics['rpy2'] = _check_rpy2()
    
    # 3. Check ADBC
    diagnostics['adbc'] = _check_adbc()
    
    # 4. Check Snowflake environment variables
    diagnostics['snowflake_env'] = _check_snowflake_env()
    
    # 5. Check disk space
    diagnostics['disk_space'] = _check_disk_space()
    
    # 6. Check network connectivity
    diagnostics['network'] = _check_network()
    
    return diagnostics


def _check_r_environment() -> Dict[str, Any]:
    """Check R environment setup."""
    result = {'ok': False, 'details': {}, 'errors': []}
    
    # Check if env directory exists
    env_exists = os.path.isdir(R_ENV_PREFIX)
    result['details']['env_exists'] = env_exists
    
    if not env_exists:
        result['errors'].append(f"R environment not found at {R_ENV_PREFIX}")
        return result
    
    # Check R_HOME
    r_home = os.environ.get('R_HOME')
    result['details']['r_home'] = r_home
    if not r_home:
        result['errors'].append("R_HOME not set")
    
    # Check R binary
    r_path = shutil.which('R')
    result['details']['r_binary'] = r_path
    if not r_path:
        result['errors'].append("R binary not in PATH")
    
    # Get R version
    if r_path:
        try:
            r_ver = subprocess.run(
                ['R', '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if r_ver.returncode == 0:
                result['details']['r_version'] = r_ver.stdout.split('\n')[0]
        except Exception as e:
            result['errors'].append(f"Failed to get R version: {e}")
    
    result['ok'] = len(result['errors']) == 0
    return result


def _check_rpy2() -> Dict[str, Any]:
    """Check rpy2 installation."""
    result = {'ok': False, 'details': {}, 'errors': []}
    
    try:
        import rpy2
        
        # Get version - handle different rpy2 versions
        try:
            # Try modern approach first (Python 3.8+)
            from importlib.metadata import version as get_version
            rpy2_version = get_version('rpy2')
        except Exception:
            # Fall back to checking module attributes
            rpy2_version = getattr(rpy2, '__version__', 'unknown')
        
        result['details']['version'] = rpy2_version
        result['details']['installed'] = True
        
        # Check if rpy2 can connect to R
        import rpy2.robjects as ro
        r_version = ro.r('R.version.string')[0]
        result['details']['r_connection'] = True
        result['details']['r_version_via_rpy2'] = r_version
        result['ok'] = True
        
    except ImportError:
        result['details']['installed'] = False
        result['errors'].append("rpy2 not installed")
    except Exception as e:
        result['details']['r_connection'] = False
        result['errors'].append(f"rpy2 cannot connect to R: {e}")
    
    return result


def _check_adbc() -> Dict[str, Any]:
    """Check ADBC installation in R."""
    result = {'ok': False, 'details': {}, 'errors': []}
    
    try:
        import rpy2.robjects as ro
        
        # Check if adbcsnowflake is installed
        check_code = '''
        list(
            adbcdrivermanager = requireNamespace("adbcdrivermanager", quietly = TRUE),
            adbcsnowflake = requireNamespace("adbcsnowflake", quietly = TRUE)
        )
        '''
        r_result = ro.r(check_code)
        
        result['details']['adbcdrivermanager'] = bool(r_result[0][0])
        result['details']['adbcsnowflake'] = bool(r_result[1][0])
        
        if not result['details']['adbcdrivermanager']:
            result['errors'].append("adbcdrivermanager not installed")
        if not result['details']['adbcsnowflake']:
            result['errors'].append("adbcsnowflake not installed")
        
        result['ok'] = all([
            result['details']['adbcdrivermanager'],
            result['details']['adbcsnowflake']
        ])
        
    except ImportError:
        result['errors'].append("rpy2 not available, cannot check ADBC")
    except Exception as e:
        result['errors'].append(f"Error checking ADBC: {e}")
    
    return result


def _check_snowflake_env() -> Dict[str, Any]:
    """Check Snowflake environment variables."""
    result = {'ok': False, 'details': {}, 'errors': []}
    
    required_vars = [
        'SNOWFLAKE_ACCOUNT',
        'SNOWFLAKE_USER',
        'SNOWFLAKE_DATABASE',
        'SNOWFLAKE_SCHEMA'
    ]
    
    optional_vars = [
        'SNOWFLAKE_WAREHOUSE',
        'SNOWFLAKE_ROLE',
        'SNOWFLAKE_PUBLIC_HOST',
        'SNOWFLAKE_PAT'
    ]
    
    missing_required = []
    for var in required_vars:
        value = os.environ.get(var)
        result['details'][var] = 'SET' if value else 'NOT SET'
        if not value:
            missing_required.append(var)
    
    for var in optional_vars:
        value = os.environ.get(var)
        result['details'][var] = 'SET' if value else 'NOT SET'
    
    if missing_required:
        result['errors'].append(f"Missing required vars: {', '.join(missing_required)}")
    
    result['ok'] = len(missing_required) == 0
    return result


def _check_disk_space() -> Dict[str, Any]:
    """Check available disk space."""
    result = {'ok': False, 'details': {}, 'errors': []}
    
    try:
        import shutil
        total, used, free = shutil.disk_usage('/')
        
        result['details']['total_gb'] = round(total / (1024**3), 2)
        result['details']['used_gb'] = round(used / (1024**3), 2)
        result['details']['free_gb'] = round(free / (1024**3), 2)
        result['details']['percent_used'] = round(used / total * 100, 1)
        
        # Warn if less than 1GB free
        min_free_gb = 1.0
        if result['details']['free_gb'] < min_free_gb:
            result['errors'].append(
                f"Low disk space: {result['details']['free_gb']}GB free "
                f"(minimum {min_free_gb}GB recommended)"
            )
        
        result['ok'] = len(result['errors']) == 0
        
    except Exception as e:
        result['errors'].append(f"Failed to check disk space: {e}")
    
    return result


def _check_network() -> Dict[str, Any]:
    """Check network connectivity to required endpoints."""
    result = {'ok': False, 'details': {}, 'errors': []}
    
    # Use URLs that reliably return 200 OK for GET requests
    # Some servers don't support HEAD requests or return non-200 for root paths
    endpoints = {
        'conda-forge': 'https://conda.anaconda.org/conda-forge/noarch/repodata.json',
        'cran': 'https://cloud.r-project.org',
        'pypi': 'https://pypi.org/simple/',
    }
    
    import urllib.request
    import urllib.error
    
    for name, url in endpoints.items():
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (diagnostic check)'}
            )
            response = urllib.request.urlopen(req, timeout=10)
            # Accept any 2xx status code
            if 200 <= response.status < 300:
                result['details'][name] = 'reachable'
            else:
                result['details'][name] = f'status: {response.status}'
        except urllib.error.HTTPError as e:
            # HTTP errors (4xx, 5xx) - server responded, so network works
            # But we still consider it an issue if we can't access resources
            if e.code in (401, 403):
                # Auth issues mean network works, server is reachable
                result['details'][name] = 'reachable (auth required)'
            else:
                result['details'][name] = f'http error: {e.code}'
                result['errors'].append(f"HTTP {e.code} from {name} ({url})")
        except urllib.error.URLError as e:
            result['details'][name] = f'unreachable: {e.reason}'
            result['errors'].append(f"Cannot reach {name} ({url})")
        except Exception as e:
            result['details'][name] = f'error: {e}'
            result['errors'].append(f"Error checking {name}: {e}")
    
    result['ok'] = len(result['errors']) == 0
    return result


def print_diagnostics(diagnostics: Optional[Dict[str, Any]] = None) -> None:
    """
    Print formatted diagnostic results.
    
    Args:
        diagnostics: Output from check_environment(), or None to run diagnostics
    """
    if diagnostics is None:
        diagnostics = check_environment()
    
    print("=" * 60)
    print("R Environment Diagnostics")
    print("=" * 60)
    
    for component, result in diagnostics.items():
        status = "[OK]" if result['ok'] else "[FAIL]"
        print(f"\n{status} {component.upper().replace('_', ' ')}")
        
        for key, value in result['details'].items():
            print(f"    {key}: {value}")
        
        if result['errors']:
            for error in result['errors']:
                print(f"    ERROR: {error}")
    
    print("\n" + "=" * 60)
    all_ok = all(r['ok'] for r in diagnostics.values())
    if all_ok:
        print("All checks passed!")
    else:
        failed = [k for k, v in diagnostics.items() if not v['ok']]
        print(f"Issues found in: {', '.join(failed)}")
    print("=" * 60)


def validate_adbc_connection() -> Tuple[bool, str]:
    """
    Validate that ADBC connection can be established.
    
    In Workspace (SNOWFLAKE_HOST set): checks for SPCS OAuth token.
    Outside Workspace: checks for PAT.
    
    Returns:
        Tuple of (success, message)
    """
    errors = []
    in_workspace = bool(os.environ.get('SNOWFLAKE_HOST'))
    
    if in_workspace:
        token_file = '/snowflake/session/token'
        if not os.path.isfile(token_file):
            errors.append(f"SPCS token file not found at {token_file}")
    else:
        pat = os.environ.get('SNOWFLAKE_PAT')
        if not pat:
            errors.append("SNOWFLAKE_PAT not set - create PAT first (or run in Workspace for auto-auth)")
    
    required = ['SNOWFLAKE_ACCOUNT', 'SNOWFLAKE_USER']
    for var in required:
        if not os.environ.get(var):
            errors.append(f"{var} not set")
    
    try:
        import rpy2.robjects as ro
        check = ro.r('requireNamespace("adbcsnowflake", quietly = TRUE)')
        if not check[0]:
            errors.append("adbcsnowflake R package not installed")
    except Exception as e:
        errors.append(f"Cannot verify ADBC packages: {e}")
    
    if errors:
        return False, "ADBC validation failed:\n  - " + "\n  - ".join(errors)
    
    auth_mode = "SPCS OAuth (internal host)" if in_workspace else "PAT (public endpoint)"
    return True, f"ADBC connection prerequisites validated ({auth_mode})"


# =============================================================================
# R Connection Management
# =============================================================================

# R code for connection management - stored as string for execution via rpy2
R_CONNECTION_CODE = '''
# =============================================================================
# Snowflake ADBC Connection Management for R
# =============================================================================
# This code provides connection pooling/reuse for ADBC connections.
# The connection is stored in the global environment as `r_sf_con`.
# =============================================================================

library(adbcdrivermanager)
library(adbcsnowflake)

#' Get or create a Snowflake ADBC connection
#' 
#' Returns the existing connection if valid, or creates a new one.
#' Connection is stored globally as `r_sf_con`.
#' 
#' @param force_new If TRUE, close existing connection and create new one
#' @return The ADBC connection object
get_snowflake_connection <- function(force_new = FALSE) {
  # Check if we need to create a new connection
  needs_new <- force_new || 
               !exists("r_sf_con", envir = .GlobalEnv) || 
               is.null(get0("r_sf_con", envir = .GlobalEnv))
  
  if (!needs_new) {
    # Verify existing connection is still valid
    tryCatch({
      con <- get("r_sf_con", envir = .GlobalEnv)
      test <- con |> read_adbc("SELECT 1")
      return(con)
    }, error = function(e) {
      message("Existing connection invalid, creating new one...")
      needs_new <<- TRUE
    })
  }
  
  if (needs_new) {
    close_snowflake_connection(silent = TRUE)
    
    account      <- Sys.getenv("SNOWFLAKE_ACCOUNT")
    user         <- Sys.getenv("SNOWFLAKE_USER")
    database     <- Sys.getenv("SNOWFLAKE_DATABASE")
    schema       <- Sys.getenv("SNOWFLAKE_SCHEMA")
    warehouse    <- Sys.getenv("SNOWFLAKE_WAREHOUSE")
    role         <- Sys.getenv("SNOWFLAKE_ROLE")
    spcs_host    <- Sys.getenv("SNOWFLAKE_HOST")
    
    # In Workspace (SPCS): use internal host + OAuth token
    # Outside Workspace: fall back to PAT + public endpoint
    if (nzchar(spcs_host) && file.exists("/snowflake/session/token")) {
      token     <- trimws(readLines("/snowflake/session/token", warn = FALSE))
      host      <- spcs_host
      auth_type <- "auth_oauth"
      message("Using SPCS OAuth via internal host: ", host)
    } else {
      token     <- Sys.getenv("SNOWFLAKE_PAT")
      host      <- Sys.getenv("SNOWFLAKE_PUBLIC_HOST")
      auth_type <- "auth_pat"
      if (identical(token, "")) {
        stop("SNOWFLAKE_PAT not set. Create PAT first using PATManager.")
      }
    }
    
    r_sf_db <<- adbc_database_init(
      adbcsnowflake::adbcsnowflake(),
      username                          = user,
      `adbc.snowflake.sql.account`      = account,
      `adbc.snowflake.sql.uri.host`     = host,
      `adbc.snowflake.sql.db`           = database,
      `adbc.snowflake.sql.schema`       = schema,
      `adbc.snowflake.sql.warehouse`    = warehouse,
      `adbc.snowflake.sql.role`         = role,
      `adbc.snowflake.sql.auth_type`                = auth_type,
      `adbc.snowflake.sql.client_option.auth_token` = token
    )
    
    r_sf_con <<- adbc_connection_init(r_sf_db)
    
    message("Snowflake ADBC connection established (r_sf_con)")
  }
  
  return(get("r_sf_con", envir = .GlobalEnv))
}

#' Close the Snowflake ADBC connection
#' 
#' Releases connection and database handles.
#' 
#' @param silent If TRUE, suppress messages
close_snowflake_connection <- function(silent = FALSE) {
  # Close connection
  if (exists("r_sf_con", envir = .GlobalEnv) && !is.null(get0("r_sf_con", envir = .GlobalEnv))) {
    tryCatch({
      adbc_connection_release(get("r_sf_con", envir = .GlobalEnv))
      if (!silent) message("ADBC connection closed")
    }, error = function(e) {
      if (!silent) message("Error closing connection: ", e$message)
    })
    rm("r_sf_con", envir = .GlobalEnv)
  }
  
  # Release database handle
  if (exists("r_sf_db", envir = .GlobalEnv) && !is.null(get0("r_sf_db", envir = .GlobalEnv))) {
    tryCatch({
      adbc_database_release(get("r_sf_db", envir = .GlobalEnv))
      if (!silent) message("ADBC database handle released")
    }, error = function(e) {
      if (!silent) message("Error releasing database: ", e$message)
    })
    rm("r_sf_db", envir = .GlobalEnv)
  }
  
  invisible(NULL)
}

#' Check if Snowflake connection exists and is valid
#' 
#' @return TRUE if connection exists and is valid
is_snowflake_connected <- function() {
  if (!exists("r_sf_con", envir = .GlobalEnv) || is.null(get0("r_sf_con", envir = .GlobalEnv))) {
    return(FALSE)
  }
  
  tryCatch({
    con <- get("r_sf_con", envir = .GlobalEnv)
    test <- con |> read_adbc("SELECT 1")
    return(TRUE)
  }, error = function(e) {
    return(FALSE)
  })
}

#' Get connection status
#' 
#' @return List with connection status details
snowflake_connection_status <- function() {
  list(
    connected = is_snowflake_connected(),
    con_exists = exists("r_sf_con", envir = .GlobalEnv),
    db_exists = exists("r_sf_db", envir = .GlobalEnv),
    account = Sys.getenv("SNOWFLAKE_ACCOUNT"),
    user = Sys.getenv("SNOWFLAKE_USER"),
    database = Sys.getenv("SNOWFLAKE_DATABASE"),
    pat_set = !identical(Sys.getenv("SNOWFLAKE_PAT"), "")
  )
}

#' Print connection status cleanly (for Workspace Notebooks)
#' 
#' Builds the entire output string before printing to avoid
#' Workspace Notebooks adding extra line breaks.
print_connection_status <- function() {
  status <- snowflake_connection_status()
  
  # Build output string all at once
  output <- paste0(
    "Connection Status:\\n",
    "  Connected:  ", status$connected, "\\n",
    "  Account:    ", status$account, "\\n",
    "  User:       ", status$user, "\\n",
    "  Database:   ", status$database, "\\n",
    "  PAT set:    ", status$pat_set, "\\n",
    "  con_exists: ", status$con_exists, "\\n",
    "  db_exists:  ", status$db_exists
  )
  
  writeLines(output)
  invisible(status)
}

message("R connection management functions loaded:")
message("  - get_snowflake_connection()    : Get or create connection (stored as r_sf_con)")
message("  - close_snowflake_connection()  : Close and release connection")
message("  - is_snowflake_connected()      : Check if connected")
message("  - snowflake_connection_status() : Get detailed status (returns list)")
message("  - print_connection_status()     : Print status cleanly")
'''


def init_r_connection_management() -> Tuple[bool, str]:
    """
    Initialize R connection management functions.
    
    This loads helper functions into R that provide connection pooling/reuse.
    The connection is stored as `r_sf_con` in R's global environment.
    
    Functions available after initialization:
    - get_snowflake_connection(): Get or create connection
    - close_snowflake_connection(): Close connection
    - is_snowflake_connected(): Check connection status
    - snowflake_connection_status(): Get detailed status
    
    Returns:
        Tuple of (success, message)
    
    Example:
        >>> success, msg = init_r_connection_management()
        >>> print(msg)
    """
    try:
        import rpy2.robjects as ro
        ro.r(R_CONNECTION_CODE)
        return True, "R connection management initialized"
    except Exception as e:
        return False, f"Failed to initialize R connection management: {e}"


def get_r_connection_status() -> Dict[str, Any]:
    """
    Get the status of the R Snowflake connection.
    
    Returns:
        Dict with connection status details
    """
    try:
        import rpy2.robjects as ro
        
        # Check if functions are loaded
        if not ro.r('exists("is_snowflake_connected")')[0]:
            return {
                'initialized': False,
                'error': 'Connection management not initialized. Call init_r_connection_management() first.'
            }
        
        # Get status from R
        status = ro.r('snowflake_connection_status()')
        return {
            'initialized': True,
            'connected': bool(status[0][0]),
            'con_exists': bool(status[1][0]),
            'db_exists': bool(status[2][0]),
            'account': str(status[3][0]),
            'user': str(status[4][0]),
            'database': str(status[5][0]),
            'pat_set': bool(status[6][0])
        }
    except Exception as e:
        return {
            'initialized': False,
            'error': str(e)
        }


def close_r_connection() -> Tuple[bool, str]:
    """
    Close the R Snowflake connection from Python.
    
    Returns:
        Tuple of (success, message)
    """
    try:
        import rpy2.robjects as ro
        
        if not ro.r('exists("close_snowflake_connection")')[0]:
            return False, "Connection management not initialized"
        
        ro.r('close_snowflake_connection()')
        return True, "R Snowflake connection closed"
    except Exception as e:
        return False, f"Error closing connection: {e}"


# =============================================================================
# Cross-Environment Support (Workspace + Local IDE)
# =============================================================================

def detect_environment() -> Tuple[str, Any]:
    """
    Detect whether running in Snowflake Workspace Notebook or local IDE.
    
    This function checks for Snowflake Workspace indicators and returns
    appropriate session or configuration for connecting to Snowflake.
    
    Returns:
        Tuple of (environment_type, session_or_config):
        - ('workspace', session) if in Workspace Notebook
        - ('local', config_dict) if in local IDE (VSCode/Cursor)
    
    Example:
        >>> env_type, config = detect_environment()
        >>> if env_type == 'workspace':
        ...     session = config
        ...     print(f"Connected as: {session.get_current_user()}")
        ... else:
        ...     print(f"Local config: {config['account']}")
    """
    # Check for Snowflake Workspace indicators
    workspace_indicators = [
        os.path.exists('/snowflake/session/token'),  # SPCS token file
        'SNOWFLAKE_HOST' in os.environ,              # Workspace env vars
        os.getcwd().startswith('/home/udf'),         # Workspace working dir
    ]
    
    if any(workspace_indicators):
        try:
            from snowflake.snowpark.context import get_active_session
            session = get_active_session()
            return ('workspace', session)
        except Exception as e:
            print(f"Warning: In Workspace but session failed: {e}")
    
    # Local IDE - return config for manual connection
    config = {
        'account': os.environ.get('SNOWFLAKE_ACCOUNT', '<YOUR_ACCOUNT>'),
        'user': os.environ.get('SNOWFLAKE_USER', '<YOUR_USER>'),
        'database': os.environ.get('SNOWFLAKE_DATABASE', '<YOUR_DATABASE>'),
        'warehouse': os.environ.get('SNOWFLAKE_WAREHOUSE', '<YOUR_WAREHOUSE>'),
        'schema': os.environ.get('SNOWFLAKE_SCHEMA', 'PUBLIC'),
        'role': os.environ.get('SNOWFLAKE_ROLE', None),
        'private_key_path': os.environ.get(
            'SNOWFLAKE_PRIVATE_KEY_PATH', 
            os.path.expanduser('~/.snowflake/keys/rsa_key.p8')
        ),
    }
    return ('local', config)


def get_snowpark_session(config: Optional[Dict[str, str]] = None):
    """
    Create a Snowpark session for the current environment.
    
    In Workspace Notebook: Returns existing active session
    In Local IDE: Creates session with key-pair or password auth
    
    Args:
        config: Optional config dict for local IDE. If not provided,
                uses detect_environment() to get config.
    
    Returns:
        Snowpark Session object
    
    Example:
        >>> session = get_snowpark_session()
        >>> print(session.get_current_user())
    """
    env_type, env_config = detect_environment()
    
    if env_type == 'workspace':
        return env_config  # Already a session
    
    # Local IDE - create session with config
    from snowflake.snowpark import Session
    
    cfg = config or env_config
    
    # Build connection parameters
    connection_parameters = {
        'account': cfg['account'],
        'user': cfg['user'],
        'database': cfg.get('database'),
        'warehouse': cfg.get('warehouse'),
        'schema': cfg.get('schema', 'PUBLIC'),
    }
    
    if cfg.get('role'):
        connection_parameters['role'] = cfg['role']
    
    # Check for key-pair auth
    key_path = cfg.get('private_key_path')
    if key_path and os.path.exists(key_path):
        with open(key_path, 'rb') as key_file:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization
            
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend()
            )
            
            private_key_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            connection_parameters['private_key'] = private_key_bytes
    elif os.environ.get('SNOWFLAKE_PASSWORD'):
        connection_parameters['password'] = os.environ['SNOWFLAKE_PASSWORD']
    else:
        raise ValueError(
            "No authentication method available. Provide either:\n"
            "  - Private key at ~/.snowflake/keys/rsa_key.p8\n"
            "  - SNOWFLAKE_PASSWORD environment variable"
        )
    
    return Session.builder.configs(connection_parameters).create()


def query_snowflake_to_r(sql: str, r_var_name: str, session=None) -> int:
    """
    Execute a Snowflake query and transfer results to R.
    
    Works in both Workspace Notebooks and local IDE.
    
    Args:
        sql: SQL query to execute
        r_var_name: Name of the R variable to store results
        session: Optional Snowpark session. If not provided, will be auto-detected.
    
    Returns:
        Number of rows transferred
    
    Example:
        >>> n_rows = query_snowflake_to_r(
        ...     "SELECT * FROM customers LIMIT 100",
        ...     "r_customers"
        ... )
        >>> print(f"Transferred {n_rows} rows to R")
    """
    import rpy2.robjects as ro
    from rpy2.robjects import pandas2ri
    from rpy2.robjects.conversion import localconverter
    
    # Get or create session
    if session is None:
        session = get_snowpark_session()
    
    # Execute query and convert to pandas
    df = session.sql(sql).to_pandas()
    
    # Transfer to R
    with localconverter(ro.default_converter + pandas2ri.converter):
        r_df = ro.conversion.py2rpy(df)
        ro.globalenv[r_var_name] = r_df
    
    return len(df)


def get_duckdb_snowflake_secret_sql(config: Dict[str, str], secret_name: str = 'sf_keypair') -> str:
    """
    Generate the CREATE SECRET SQL for DuckDB Snowflake extension.
    
    Args:
        config: Dict with account, user, database, warehouse, and private key
        secret_name: Name for the secret (default: 'sf_keypair')
    
    Returns:
        SQL string for CREATE SECRET statement
    
    Example:
        >>> config = {'account': 'xy12345', 'user': 'MYUSER', ...}
        >>> sql = get_duckdb_snowflake_secret_sql(config)
        >>> dbExecute(con, sql)
    """
    # Read private key if path provided
    private_key = config.get('private_key', '')
    if not private_key and config.get('private_key_path'):
        key_path = os.path.expanduser(config['private_key_path'])
        if os.path.exists(key_path):
            with open(key_path, 'r') as f:
                private_key = f.read()
    
    # Escape single quotes in key
    private_key_escaped = private_key.replace("'", "''")
    
    sql = f"""
CREATE OR REPLACE SECRET {secret_name} (
    TYPE snowflake,
    ACCOUNT '{config['account']}',
    USER '{config['user']}',
    DATABASE '{config.get('database', '')}',
    WAREHOUSE '{config.get('warehouse', '')}',
    AUTH_TYPE 'key_pair',
    PRIVATE_KEY '{private_key_escaped}'
)"""
    return sql.strip()


def print_environment_info():
    """
    Print information about the current environment.
    
    Useful for debugging and verification.
    """
    env_type, config = detect_environment()
    
    print(f"Environment: {env_type.upper()}")
    print("-" * 40)
    
    if env_type == 'workspace':
        session = config
        print(f"Account:   {session.sql('SELECT CURRENT_ACCOUNT()').collect()[0][0]}")
        print(f"User:      {session.sql('SELECT CURRENT_USER()').collect()[0][0]}")
        print(f"Role:      {session.get_current_role()}")
        print(f"Database:  {session.get_current_database()}")
        print(f"Schema:    {session.get_current_schema()}")
        print(f"Warehouse: {session.get_current_warehouse()}")
    else:
        print(f"Account:   {config['account']}")
        print(f"User:      {config['user']}")
        print(f"Database:  {config.get('database', 'Not set')}")
        print(f"Warehouse: {config.get('warehouse', 'Not set')}")
        
        key_path = config.get('private_key_path', '')
        key_status = "Found" if os.path.exists(key_path) else "NOT FOUND"
        print(f"Key Path:  {key_path} ({key_status})")
