"""
Scala & Java Environment Helpers for Snowflake Workspace Notebooks

This module provides:
- JVM startup via JPype (in-process, shared memory)
- Scala REPL initialization (IMain or Ammonite)
- JShell initialization for Java (JDK 17 built-in REPL)
- %%scala cell magic and %scala line magic for Scala execution
- %%java cell magic and %java line magic for Java execution
- -i / -o flags for Python <-> Scala/Java variable transfer
- Auto-detected DataFrame interop for Snowpark (Scala & Java) and PySpark DFs
- Cross-API transfers via -i:spark / -o:snowpark type hints
- Grid viewer: auto-detect Snowpark DataFrames and render via Workspace grid
  (SQL plan transfer → Python .to_pandas(); --text to opt out, --df to pick var)
- Snowpark session credential injection (SPCS OAuth + PAT fallback)
- Snowpark Connect for Scala (opt-in): local Spark Connect gRPC server
  + Scala SparkSession bound as `spark` in %%scala cells
- Environment diagnostics

Architecture:
    Python Kernel  <-->  JVM (in-process via JPype/JNI)
                            - Scala IMain or Ammonite REPL
                            - JShell REPL (Java)
                            - Snowpark Scala / Java session
                            - User Scala / Java code execution

Usage:
    from scala_helpers import setup_scala_environment

    result = setup_scala_environment()

    # Then in notebook cells:
    # %%scala
    # val x = 42
    # println(s"The answer is $x")

    # %%java
    # int x = 42;
    # System.out.println("The answer is " + x);

    # Single-line:
    # %scala println("Hello!")
    # %java System.out.println("Hello!");

    # With variable transfer (like rpy2's %%R -i / -o):
    # %%scala -i count -o total
    # val total = (1 to count.asInstanceOf[Int]).sum

    # DataFrame interop (auto-detected — Snowpark and PySpark):
    # py_df = session.table("my_table")   # Python Snowpark DF
    # %%scala -i py_df -o result_df
    # val result_df = py_df.filter(col("id") > 10)
    # # result_df is now a Snowpark Python DataFrame in the notebook
    #
    # Cross-API with type hints:
    # %%scala -i:spark py_df -o:snowpark spark_result
    # val spark_result = py_df.groupBy("region").count()

After setup with Snowpark session:
    from scala_helpers import inject_session_credentials
    inject_session_credentials(session)

    # %%scala
    # import com.snowflake.snowpark._
    # val sf = Session.builder.configs(Map(
    #   "URL" -> System.getProperty("SNOWFLAKE_URL"), ...
    # )).create
    # sf.sql("SELECT CURRENT_USER()").show()
"""

import os
import sys
import json
import subprocess
import shutil
from typing import Optional, Dict, Any, Tuple, List


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_JAR_DIR = os.path.expanduser("~/scala_jars")
DEFAULT_METADATA_FILE = os.path.join(DEFAULT_JAR_DIR, "scala_env_metadata.json")
def _resolve_jvm_env_prefix() -> str:
    """Return the JVM environment prefix.

    Checks ``~/.workspace_env_prefix`` first (written by the combined
    setup_workspace_environment.sh script), then falls back to the
    per-language default.
    """
    marker = os.path.expanduser("~/.workspace_env_prefix")
    if os.path.isfile(marker):
        prefix = open(marker).read().strip()
        if prefix and os.path.isdir(prefix):
            return prefix
    return "/root/.local/share/mamba/envs/jvm_env"

DEFAULT_JVM_ENV_PREFIX = _resolve_jvm_env_prefix()


class MagicExecutionError(Exception):
    """Raised when a %%scala or %%java magic cell fails.

    Raising this (rather than just printing to stderr) ensures that
    Jupyter's "Run All" stops at the failing cell instead of silently
    continuing.
    """


# Populated after setup
_scala_state = {
    "jvm_started": False,
    "interpreter": None,
    "interpreter_type": None,  # "imain" or "ammonite"
    "output_stream": None,
    "magic_registered": False,
    "metadata": None,
}


# =============================================================================
# Environment Setup
# =============================================================================

def setup_scala_environment(
    register_magic: bool = True,
    register_java_magic: bool = True,
    prefer_ammonite: bool = True,
    install_jpype: bool = True,
    metadata_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Configure the Python environment for Scala and Java execution via JPype.

    This function:
    1. Loads environment metadata from the setup script
    2. Sets JAVA_HOME and PATH
    3. Installs JPype if needed
    4. Starts the JVM with the correct classpath
    5. Initializes a Scala REPL (Ammonite or IMain)
    6. Initializes a JShell interpreter for Java
    7. Registers the %%scala and %%java IPython magics

    Args:
        register_magic: Whether to register the %%scala magic (default: True)
        register_java_magic: Whether to register the %%java magic (default: True)
        prefer_ammonite: Try Ammonite first, fall back to IMain (default: True)
        install_jpype: Whether to pip-install JPype1 if missing (default: True)
        metadata_file: Path to scala_env_metadata.json (default: auto-detect)

    Returns:
        Dict with setup status and details
    """
    result = {
        "success": False,
        "java_home": None,
        "java_version": None,
        "scala_version": None,
        "interpreter_type": None,
        "jpype_installed": False,
        "jvm_started": False,
        "magic_registered": False,
        "java_magic_registered": False,
        "jshell_initialized": False,
        "spark_connect_enabled": False,
        "errors": [],
    }

    # --- Load metadata ---
    meta_path = metadata_file or DEFAULT_METADATA_FILE
    metadata = _load_metadata(meta_path)
    if metadata is None:
        result["errors"].append(
            f"Metadata file not found at {meta_path}. "
            "Run 'bash setup_scala_environment.sh' first."
        )
        return result

    _scala_state["metadata"] = metadata
    result["java_home"] = metadata.get("java_home")
    result["scala_version"] = metadata.get("scala_full_version") or metadata.get("scala_version")

    # --- Set environment variables ---
    env_prefix = metadata["env_prefix"]
    os.environ["JAVA_HOME"] = metadata["java_home"]
    os.environ["PATH"] = f"{env_prefix}/bin:" + os.environ.get("PATH", "")

    # Verify Java
    java_path = shutil.which("java")
    if not java_path:
        result["errors"].append("Java binary not found in PATH after configuration")
        return result

    try:
        java_ver = subprocess.run(
            ["java", "-version"], capture_output=True, text=True, timeout=10
        )
        result["java_version"] = java_ver.stderr.split("\n")[0]
    except Exception as e:
        result["errors"].append(f"Failed to get Java version: {e}")

    # --- Install JPype ---
    if install_jpype:
        try:
            import jpype
        except ImportError:
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "JPype1", "-q"],
                    check=True, capture_output=True, timeout=120,
                )
            except Exception as e:
                result["errors"].append(f"Failed to install JPype1: {e}")
                return result

    try:
        import jpype  # noqa: F811
        result["jpype_installed"] = True
    except ImportError:
        result["errors"].append(
            "JPype1 not available after install attempt"
        )
        return result

    # --- Set JAVA_TOOL_OPTIONS (must happen before any JVM start) ---
    _add_opens = [
        "--add-opens=java.base/java.nio=ALL-UNNAMED",
        "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.util=ALL-UNNAMED",
    ]
    os.environ["JAVA_TOOL_OPTIONS"] = " ".join(_add_opens)

    # --- Start JVM (we control the classpath) ---
    classpath = _build_classpath(
        metadata, include_ammonite=prefer_ammonite
    )
    jvm_options = _resolve_jvm_options(metadata)
    result["jvm_options"] = jvm_options

    if not jpype.isJVMStarted():
        try:
            jpype.startJVM(
                jpype.getDefaultJVMPath(),
                *jvm_options,
                classpath=classpath,
                convertStrings=True,
            )
            import jpype.imports  # noqa: F811 — registers Java import hooks
            _scala_state["jvm_started"] = True
            result["jvm_started"] = True
        except Exception as e:
            result["errors"].append(f"Failed to start JVM: {e}")
            return result
    else:
        import jpype.imports  # noqa: F811
        _scala_state["jvm_started"] = True
        result["jvm_started"] = True

        # JVM is already running — dynamically add any new classpath entries
        # (e.g. Spark Connect JARs added on a re-install).  We update both
        # JPype's classloader AND the java.class.path system property so
        # that a freshly created IMain (with usejavacp=true) sees them.
        System = jpype.JClass("java.lang.System")
        current_cp = str(System.getProperty("java.class.path") or "")
        existing = set(current_cp.split(os.pathsep)) if current_cp else set()
        new_entries = [p for p in classpath if p not in existing]
        if new_entries:
            for entry in new_entries:
                jpype.addClassPath(entry)
            new_cp = os.pathsep.join(new_entries)
            if current_cp:
                merged = current_cp + os.pathsep + new_cp
            else:
                merged = new_cp
            System.setProperty("java.class.path", merged)

    # --- Start Spark Connect server (after JVM, with monkey-patch) ---
    # snowpark_connect.start_session() internally calls start_jvm() which
    # refuses to run when a JVM is already active. We need OUR JVM (with
    # the full Snowpark/Ammonite/Scala classpath) so we start it first,
    # then patch start_jvm to be a no-op. The gRPC server uses the existing
    # JVM — it communicates over TCP so same-process is fine.
    spark_connect_enabled = metadata.get("spark_connect_enabled", False)
    result["spark_connect_enabled"] = spark_connect_enabled

    if spark_connect_enabled:
        _start_spark_connect_server_early(metadata, result)

    # --- Initialize Scala interpreter ---
    if prefer_ammonite and _has_ammonite_jars(metadata):
        try:
            _init_ammonite(metadata)
            result["interpreter_type"] = "ammonite"
        except Exception as e:
            result["errors"].append(
                f"Ammonite init failed ({e}); falling back to IMain"
            )
            try:
                _init_imain(metadata)
                result["interpreter_type"] = "imain"
                # Clear the non-fatal Ammonite error
                result["errors"] = [
                    err for err in result["errors"]
                    if "Ammonite init failed" not in err
                ]
            except Exception as e2:
                result["errors"].append(f"IMain init also failed: {e2}")
                return result
    else:
        try:
            _init_imain(metadata)
            result["interpreter_type"] = "imain"
        except Exception as e:
            result["errors"].append(f"IMain init failed: {e}")
            return result

    # --- Register Scala magic ---
    if register_magic:
        try:
            _register_scala_magic()
            result["magic_registered"] = True
            _scala_state["magic_registered"] = True
        except Exception as e:
            result["errors"].append(f"Failed to register %%scala magic: {e}")

    # --- Initialize JShell for Java ---
    try:
        _init_jshell(metadata)
        result["jshell_initialized"] = True
    except Exception as e:
        result["errors"].append(f"JShell init failed: {e}")

    # --- Register Java magic ---
    if register_java_magic and result["jshell_initialized"]:
        try:
            _register_java_magic()
            result["java_magic_registered"] = True
            _java_state["magic_registered"] = True
        except Exception as e:
            result["errors"].append(f"Failed to register %%java magic: {e}")

        # Also register %%java_udf / %register_java_udf
        try:
            _register_java_udf_magics()
        except Exception as e:
            result["errors"].append(f"Failed to register Java UDF magics: {e}")

    result["spark_connect_enabled"] = metadata.get("spark_connect_enabled", False)
    result["success"] = len(result["errors"]) == 0
    return result


# =============================================================================
# JVM Heap Sizing
# =============================================================================

def _detect_total_memory_mb() -> Optional[int]:
    """Read total physical memory from /proc/meminfo (Linux only)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024  # kB -> MB
    except (OSError, ValueError):
        pass
    return None


def _compute_heap_size(
    fraction: float = 0.25,
    min_mb: int = 1024,
    max_mb: int = 4096,
) -> str:
    """
    Compute a JVM -Xmx heap size as a fraction of total system RAM.

    The JVM shares the process with Python + Scala compiler +
    Snowpark, so we default to 25% of total memory, clamped
    between 1GB and 4GB.

    Returns:
        Heap string like "2g" or "1536m"
    """
    total = _detect_total_memory_mb()
    if total is None:
        return f"{min_mb}m"

    heap_mb = int(total * fraction)
    heap_mb = max(min_mb, min(heap_mb, max_mb))

    if heap_mb >= 1024 and heap_mb % 1024 == 0:
        return f"{heap_mb // 1024}g"
    return f"{heap_mb}m"


def _resolve_jvm_options(metadata: Dict[str, Any]) -> List[str]:
    """
    Build the final JVM option list from metadata, resolving any
    'auto' heap sizing.
    """
    opts = metadata.get("jvm_options", [
        "--add-opens=java.base/java.nio=ALL-UNNAMED",
    ])

    heap_setting = metadata.get("jvm_heap", "auto")

    has_xmx = any(o.startswith("-Xmx") for o in opts)

    if heap_setting == "auto" and not has_xmx:
        heap = _compute_heap_size()
        opts = [f"-Xmx{heap}"] + opts
    elif heap_setting != "auto" and not has_xmx:
        opts = [f"-Xmx{heap_setting}"] + opts

    return opts


# =============================================================================
# Metadata & Classpath
# =============================================================================

def _load_metadata(path: str) -> Optional[Dict[str, Any]]:
    """Load the environment metadata JSON written by the setup script."""
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def _read_classpath_file(path: str) -> List[str]:
    """Read a colon-separated classpath file into a list of paths."""
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r") as f:
        content = f.read().strip()
    if not content:
        return []
    return [p for p in content.split(":") if p and os.path.exists(p)]


def _build_classpath(
    metadata: Dict[str, Any], include_ammonite: bool = True
) -> List[str]:
    """Build the full classpath from metadata files."""
    cp = []

    # Scala compiler/library
    cp.extend(_read_classpath_file(metadata.get("scala_classpath_file", "")))

    # Ammonite (if requested and available)
    if include_ammonite:
        cp.extend(_read_classpath_file(metadata.get("ammonite_classpath_file", "")))

    # Snowpark and its transitive dependencies
    cp.extend(_read_classpath_file(metadata.get("snowpark_classpath_file", "")))

    # Extra dependencies
    cp.extend(_read_classpath_file(metadata.get("extra_classpath_file", "")))

    # Spark Connect client JARs (opt-in)
    if metadata.get("spark_connect_enabled"):
        cp.extend(_read_classpath_file(
            metadata.get("spark_connect_classpath_file", "")
        ))

        # PySpark's bundled JARs (spark-sql, spark-catalyst, etc.)
        # are needed by the snowpark_connect server at runtime.
        try:
            import pyspark
            pyspark_jars_dir = os.path.join(
                os.path.dirname(pyspark.__file__), "jars"
            )
            if os.path.isdir(pyspark_jars_dir):
                import glob
                cp.extend(sorted(glob.glob(
                    os.path.join(pyspark_jars_dir, "*.jar")
                )))
        except ImportError:
            pass

    return cp


def _start_spark_connect_server_early(
    metadata: Dict[str, Any], result: Dict[str, Any]
) -> None:
    """Start the Spark Connect gRPC server using the already-running JVM.

    snowpark_connect.start_session() internally calls start_jvm() which
    refuses to run when a JVM is already active. We monkey-patch start_jvm
    to be a no-op so the server reuses our JVM (which has the full
    Snowpark/Ammonite/Scala classpath).
    """
    import socket
    import threading
    import time

    port = metadata.get("spark_connect_server_port", 15002)

    def _port_open():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (socket.error, OSError):
            return False

    if _port_open():
        return  # already running

    import time as _time

    # Set timezone from Snowflake session before snowpark_connect
    # reads it (prevents "timezone fallback" message).
    try:
        from snowflake.snowpark.context import get_active_session
        session = get_active_session()
    except Exception:
        session = None

    if session and not os.environ.get("TZ"):
        tz_val = "UTC"
        try:
            rows = session.sql(
                "SELECT CURRENT_TIMEZONE() AS tz"
            ).collect()
            if rows:
                tz_val = (
                    str(rows[0]["TZ"]).strip() or "UTC"
                )
        except Exception:
            pass
        os.environ["TZ"] = tz_val
        try:
            _time.tzset()
        except AttributeError:
            pass

    try:
        import snowflake.snowpark_connect as spc
        import snowflake.snowpark_connect.server as spc_srv
    except ImportError:
        result["errors"].append(
            "spark_connect.enabled is true but snowpark-connect "
            "is not installed. Re-run setup_scala_environment.sh"
        )
        return

    # Monkey-patch start_jvm so the server reuses our JVM.
    _original_start_jvm = spc_srv.start_jvm

    def _patched_start_jvm():
        import jpype
        if jpype.isJVMStarted():
            return
        _original_start_jvm()

    spc_srv.start_jvm = _patched_start_jvm

    server_error = [None]

    def _run():
        try:
            spc.start_session(
                is_daemon=False,
                remote_url=f"sc://localhost:{port}",
                snowpark_session=session,
            )
        except Exception as e:
            server_error[0] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    for attempt in range(30):
        _time.sleep(1)
        if server_error[0]:
            result["errors"].append(
                f"Spark Connect server failed: "
                f"{server_error[0]}"
            )
            spc_srv.start_jvm = _original_start_jvm
            return
        if _port_open():
            print(
                f"  Spark Connect server listening on "
                f"localhost:{port}"
            )
            break
    else:
        result["errors"].append(
            "Spark Connect server did not start within 30s"
        )

    spc_srv.start_jvm = _original_start_jvm


def _has_ammonite_jars(metadata: Dict[str, Any]) -> bool:
    """Check whether Ammonite JARs were resolved during setup."""
    amm_file = metadata.get("ammonite_classpath_file", "")
    if not amm_file or not os.path.isfile(amm_file):
        return False
    with open(amm_file, "r") as f:
        content = f.read().strip()
    return len(content) > 10  # non-empty classpath


# =============================================================================
# Scala Interpreter: IMain (Option A)
# =============================================================================

def _init_imain(metadata: Dict[str, Any]) -> None:
    """Initialize the Scala IMain interpreter via JPype."""
    import jpype

    from java.io import PrintWriter, ByteArrayOutputStream
    Settings = jpype.JClass("scala.tools.nsc.Settings")
    IMain = jpype.JClass("scala.tools.nsc.interpreter.IMain")

    settings = Settings()
    settings.usejavacp().tryToSetFromPropertyValue("true")

    # Wrap REPL code in classes (not objects) so that lambdas defined
    # in the REPL can be serialized for UDF upload.
    # See: docs.snowflake.com/.../quickstart-jupyter  step 3
    try:
        settings.YreplClassBased().tryToSetFromPropertyValue("true")
    except Exception:
        # Fall back to command-line-style flag if the setting accessor
        # name differs across Scala versions
        try:
            settings.processArgumentString("-Yrepl-class-based")
        except Exception:
            pass

    # Configure REPL class output directory for UDF serialization
    repl_class_dir = os.path.join(
        metadata.get("jar_dir", DEFAULT_JAR_DIR), "replClasses"
    )
    os.makedirs(repl_class_dir, exist_ok=True)
    settings.outputDirs().setSingleOutput(repl_class_dir)

    output_stream = ByteArrayOutputStream()
    print_writer = PrintWriter(output_stream, True)

    interpreter = IMain(settings, print_writer)

    _scala_state["interpreter"] = interpreter
    _scala_state["interpreter_type"] = "imain"
    _scala_state["output_stream"] = output_stream
    _scala_state["_repl_class_dir"] = repl_class_dir

    # Warm up the interpreter (first compilation is slow)
    _execute_scala_imain('println("IMain ready")')


def _execute_scala_imain(code: str) -> Tuple[bool, str, str]:
    """
    Execute Scala code via IMain.

    Returns:
        (success, stdout_output, error_output)
    """
    import jpype

    interpreter = _scala_state["interpreter"]
    output_stream = _scala_state["output_stream"]

    # Capture System.out/System.err for println output
    from java.io import ByteArrayOutputStream as BAOS, PrintStream
    old_out = jpype.JClass("java.lang.System").out
    old_err = jpype.JClass("java.lang.System").err
    sys_out_capture = BAOS()
    sys_err_capture = BAOS()
    jpype.JClass("java.lang.System").setOut(PrintStream(sys_out_capture, True))
    jpype.JClass("java.lang.System").setErr(PrintStream(sys_err_capture, True))

    try:
        output_stream.reset()
        result = interpreter.interpret(code)
        result_str = str(result)
        success = "Success" in result_str
    finally:
        jpype.JClass("java.lang.System").setOut(old_out)
        jpype.JClass("java.lang.System").setErr(old_err)

    repl_output = str(output_stream.toString()).strip()
    stdout_text = str(sys_out_capture.toString()).strip()
    stderr_text = str(sys_err_capture.toString()).strip()

    # Combine all output sources
    parts = []
    if stdout_text:
        parts.append(stdout_text)
    if repl_output:
        parts.append(repl_output)
    combined = "\n".join(parts)

    return success, combined, stderr_text


# =============================================================================
# Scala Interpreter: Ammonite (Option A2)
# =============================================================================

def _init_ammonite(metadata: Dict[str, Any]) -> None:
    """
    Initialize the Ammonite REPL interpreter via JPype.

    Ammonite's programmatic embedding API is less documented than IMain's.
    This function attempts several initialization strategies.
    """
    import jpype

    # Strategy: Use Ammonite's Main class to create an interpreter.
    # The exact API depends on the Ammonite version.
    #
    # Ammonite 3.x provides ammonite.Main with methods:
    #   - Main.main(args) — CLI entry point (not useful for embedding)
    #   - Instantiate via constructor and use runCode/runScript
    #
    # Alternatively, we can access the underlying IMain that Ammonite wraps
    # and use it directly, getting the best of both worlds.

    try:
        jpype.JClass("ammonite.Main")
    except Exception:
        raise RuntimeError(
            "Ammonite classes not found on classpath. "
            "Ensure Ammonite JARs were resolved by setup."
        )

    # Ammonite's Main has various static factory methods and constructors.
    # We try to create a headless instance suitable for programmatic use.
    #
    # We use a pragmatic approach:
    # 1. Try to instantiate Ammonite's Main and call its run* methods
    # 2. If that fails, extract the underlying Scala IMain from Ammonite
    # 3. Last resort: fall back to plain IMain

    # Attempt: Use ammonite.main.Scripts or ammonite.interp.Interpreter
    # Pragmatic approach: Use IMain but with Ammonite JARs on the
    # classpath so that Ammonite classes are accessible. This is
    # "ammonite-lite" — IMain + pre-loaded Ammonite/Snowpark JARs.
    # Full Ammonite embedding (runtime import $ivy) is a stretch goal.
    try:
        _init_imain(metadata)
        _scala_state["interpreter_type"] = "ammonite-lite"

        snowpark_cp = metadata.get("snowpark_classpath_file", "")
        if snowpark_cp and os.path.isfile(snowpark_cp):
            _execute_scala_imain(
                "import com.snowflake.snowpark._\n"
                "import com.snowflake.snowpark.functions._"
            )
        return

    except Exception as e:
        raise RuntimeError(
            f"Ammonite-lite initialization failed: {e}"
        )


# =============================================================================
# Java Interpreter: JShell (JDK 17+)
# =============================================================================

# Populated after JShell init
_java_state: Dict[str, Any] = {
    "jshell": None,
    "magic_registered": False,
}


def _init_jshell(metadata: Dict[str, Any]) -> None:
    """Initialize a JShell interpreter via JPype.

    JShell ships with JDK 9+ so no extra JARs are needed.  We use the
    ``local`` execution engine so that JShell runs in-process, sharing
    the same JVM, classpath, and System properties as the Scala REPL.
    """
    import jpype

    JShell = jpype.JClass("jdk.jshell.JShell")
    jshell = JShell.builder().executionEngine("local").build()

    # Feed the full classpath so Snowpark Java classes are available
    cp = _build_classpath(metadata, include_ammonite=False)
    for entry in cp:
        if entry and os.path.exists(entry):
            jshell.addToClasspath(entry)

    _java_state["jshell"] = jshell

    # Warm-up: first eval is slow because it triggers classloading
    _execute_jshell('System.out.println("JShell ready");')


def _execute_jshell(code: str) -> Tuple[bool, str, str]:
    """Execute Java code via JShell.

    JShell's ``eval()`` processes one *snippet* at a time.  We pass the
    entire cell contents and iterate over the returned ``SnippetEvent``
    list.

    Returns:
        (success, stdout_output, error_output)
    """
    import jpype

    jshell = _java_state["jshell"]
    if jshell is None:
        return False, "", "JShell not initialized. Call setup_scala_environment() first."

    Snippet = jpype.JClass("jdk.jshell.Snippet")
    Status = Snippet.Status
    Kind = Snippet.Kind
    sca = jshell.sourceCodeAnalysis()
    Completeness = jpype.JClass(
        "jdk.jshell.SourceCodeAnalysis$Completeness"
    )

    # Redirect System.out / System.err to capture println output
    from java.io import ByteArrayOutputStream as BAOS, PrintStream
    old_out = jpype.JClass("java.lang.System").out
    old_err = jpype.JClass("java.lang.System").err
    sys_out_capture = BAOS()
    sys_err_capture = BAOS()
    jpype.JClass("java.lang.System").setOut(PrintStream(sys_out_capture, True))
    jpype.JClass("java.lang.System").setErr(PrintStream(sys_err_capture, True))

    all_ok = True
    value_parts: list = []
    error_parts: list = []

    try:
        # Strip leading/trailing whitespace but preserve internal structure
        remaining = code.strip()
        while remaining:
            # Use SourceCodeAnalysis to find the next complete snippet
            info = sca.analyzeCompletion(remaining)
            source = str(info.source())
            remaining = str(info.remaining()).strip()

            if not source.strip():
                break

            # Skip pure comment lines that aren't valid snippets
            completeness = info.completeness()
            if completeness == Completeness.EMPTY:
                continue

            events = jshell.eval(source)
            for event in events:
                status = event.status()
                if status == Status.VALID:
                    snippet = event.snippet()
                    # Only show return values for standalone expressions,
                    # not for variable declarations, imports, methods, etc.
                    if snippet is not None and snippet.kind() == Kind.EXPRESSION:
                        val = event.value()
                        if val is not None:
                            val_str = str(val).strip()
                            if val_str:
                                value_parts.append(val_str)
                elif status == Status.REJECTED:
                    all_ok = False
                    diag_iter = jshell.diagnostics(event.snippet()).iterator()
                    while diag_iter.hasNext():
                        d = diag_iter.next()
                        error_parts.append(str(d.getMessage(None)))
                    if not error_parts:
                        error_parts.append(
                            f"Snippet rejected: {str(event.snippet().source()).strip()}"
                        )
                ex = event.exception()
                if ex is not None:
                    all_ok = False
                    error_parts.append(str(ex.getMessage()))

            if not all_ok:
                break
    finally:
        jpype.JClass("java.lang.System").setOut(old_out)
        jpype.JClass("java.lang.System").setErr(old_err)

    stdout_text = str(sys_out_capture.toString()).strip()
    stderr_text = str(sys_err_capture.toString()).strip()

    parts = []
    if stdout_text:
        parts.append(stdout_text)
    if value_parts:
        parts.append("\n".join(value_parts))
    combined = "\n".join(parts)

    if stderr_text:
        if error_parts:
            error_parts.insert(0, stderr_text)
        else:
            error_parts.append(stderr_text)

    return all_ok, combined, "\n".join(error_parts)


def execute_java(code: str) -> Tuple[bool, str, str]:
    """Execute Java code in the JShell interpreter.

    Returns:
        (success, output, errors)
    """
    if _java_state.get("jshell") is None:
        return False, "", "No JShell interpreter initialized. Call setup_scala_environment() first."
    return _execute_jshell(code)


# =============================================================================
# Unified Execution Interface
# =============================================================================

def execute_scala(code: str) -> Tuple[bool, str, str]:
    """
    Execute Scala code in the active interpreter.

    Returns:
        (success, output, errors)
    """
    itype = _scala_state.get("interpreter_type")
    if itype in ("imain", "ammonite-lite"):
        return _execute_scala_imain(code)
    elif itype == "ammonite":
        # Full Ammonite — use its eval API
        return _execute_scala_imain(code)  # same for now
    else:
        return False, "", "No Scala interpreter initialized. Call setup_scala_environment() first."


# =============================================================================
# %%scala IPython Magic
# =============================================================================

def _parse_magic_args(line: str) -> Dict[str, Any]:
    """
    Parse flags from the ``%%scala`` / ``%scala`` magic line.

    Supported flags (modelled on rpy2's ``%%R``):
        -i var1,var2        Push Python variables into Scala (auto-detect type)
        -i:spark var        Push as Spark DataFrame (cross-API if source is Snowpark)
        -i:snowpark var     Push as Snowpark DataFrame (default for Snowpark DFs)
        -o var1,var2        Pull Scala variables into Python (auto-detect type)
        -o:spark var        Pull as PySpark DataFrame
        -o:snowpark var     Pull as Snowpark Python DataFrame (cross-API if source is Spark)
        --silent            Suppress REPL variable-binding echo
        --time              Print wall-clock execution time
        --text              Suppress grid viewer (text output only)
        --df varname        Explicitly pick which Scala variable to grid-display
        --grid-limit N      Max rows for grid display (default: 1000)

    Returns:
        Dict with keys: inputs, outputs, silent, show_time, text_only,
        df_varname, grid_limit.
        inputs/outputs are lists of (name, type_hint) tuples where
        type_hint is None (auto), "spark", or "snowpark".
    """
    import shlex

    args: Dict[str, Any] = {
        "inputs": [],
        "outputs": [],
        "silent": False,
        "show_time": False,
        "text_only": False,
        "df_varname": None,
        "grid_limit": 1000,
    }

    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # Match -i, -i:spark, -i:snowpark (and same for -o)
        io_hint = None
        io_key = None
        if tok in ("-i", "-i:spark", "-i:snowpark"):
            io_key = "inputs"
            if tok == "-i:spark":
                io_hint = "spark"
            elif tok == "-i:snowpark":
                io_hint = "snowpark"
        elif tok in ("-o", "-o:spark", "-o:snowpark"):
            io_key = "outputs"
            if tok == "-o:spark":
                io_hint = "spark"
            elif tok == "-o:snowpark":
                io_hint = "snowpark"

        if io_key is not None and i + 1 < len(tokens):
            names = [v.strip() for v in tokens[i + 1].split(",") if v.strip()]
            args[io_key].extend((n, io_hint) for n in names)
            i += 2
        elif tok == "--silent":
            args["silent"] = True
            i += 1
        elif tok == "--time":
            args["show_time"] = True
            i += 1
        elif tok == "--text":
            args["text_only"] = True
            i += 1
        elif tok == "--df" and i + 1 < len(tokens):
            args["df_varname"] = tokens[i + 1]
            i += 2
        elif tok == "--grid-limit" and i + 1 < len(tokens):
            try:
                args["grid_limit"] = int(tokens[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    return args


def _push_inputs(items: List[Tuple[str, Optional[str]]]) -> None:
    """Push Python variables into the Scala interpreter.

    Each *item* is a ``(name, type_hint)`` tuple where *type_hint* is
    ``None`` (auto-detect), ``"spark"``, or ``"snowpark"``.

    Dispatch logic:
      - hint "spark" + Snowpark DF  → cross-API via ``spark.sql(sql_plan)``
      - hint "spark" + PySpark DF   → same-API via temp view
      - hint "snowpark" (or auto) + Snowpark DF → same-API via ``sfSession.sql()``
      - hint "snowpark" + PySpark DF → not yet supported (warn)
      - auto + PySpark DF → same-API via temp view (``spark.table()``)
      - primitives/strings → direct bind regardless of hint
    """
    ip = get_ipython()  # type: ignore[name-defined]  # noqa: F821
    for name, hint in items:
        if name not in ip.user_ns:
            print(
                f"Warning: Python variable '{name}' not found, skipping",
                file=sys.stderr,
            )
            continue

        value = ip.user_ns[name]

        if _is_snowpark_python_df(value):
            if hint == "spark":
                _push_snowpark_df_as_spark(name, value)
            else:
                _push_snowpark_df(name, value)
        elif _is_pyspark_df(value):
            if hint == "snowpark":
                print(
                    f"Warning: PySpark DF → Snowpark Scala is not supported; "
                    f"pushing '{name}' as Spark DF instead",
                    file=sys.stderr,
                )
            _push_pyspark_df(name, value)
        else:
            push_to_scala(name, value)


def _pull_outputs(items: List[Tuple[str, Optional[str]]]) -> None:
    """Pull Scala variables back into the Python namespace.

    Each *item* is a ``(name, type_hint)`` tuple where *type_hint* is
    ``None`` (auto-detect), ``"spark"``, or ``"snowpark"``.

    Dispatch logic:
      - Snowpark Scala DF + hint "spark"    → not typical (warn, use default)
      - Snowpark Scala DF + auto/snowpark   → SQL plan transfer (existing)
      - Spark Scala DF + hint "snowpark"    → cross-API via transient table
      - Spark Scala DF + auto/spark         → same-API via temp view → PySpark DF
      - primitives                          → direct extract
    """
    ip = get_ipython()  # type: ignore[name-defined]  # noqa: F821
    for name, hint in items:
        if _is_snowpark_scala_df(name):
            py_df = _pull_snowpark_df(name)
            if py_df is not None:
                ip.user_ns[name] = py_df
                continue
        elif _is_spark_scala_df(name):
            if hint == "snowpark":
                py_df = _pull_spark_df_as_snowpark(name)
            else:
                py_df = _pull_spark_df(name)
            if py_df is not None:
                ip.user_ns[name] = py_df
                continue

        val = pull_from_scala(name)
        if val is None:
            print(f"Warning: Scala variable '{name}' not found, skipping",
                  file=sys.stderr)
        else:
            ip.user_ns[name] = val


def _detect_last_scala_df(repl_output: str) -> Optional[str]:
    """Detect whether the last REPL binding is a Snowpark DataFrame.

    IMain echoes type information for every val/expression result, e.g.::

        df: com.snowflake.snowpark.DataFrame = DataFrame[COL1: StringType]
        res0: com.snowflake.snowpark.DataFrame = DataFrame[...]

    We scan lines in reverse and return the variable name from the last
    line that matches the Snowpark DataFrame type signature, or ``None``.
    """
    import re

    pattern = re.compile(
        r"^(\w+):\s+com\.snowflake\.snowpark\.DataFrame\b"
    )
    for line in reversed(repl_output.splitlines()):
        line = line.strip()
        m = pattern.match(line)
        if m:
            return m.group(1)
    return None


def _scala_df_to_pandas(
    var_name: str,
    limit: int = 1000,
) -> Optional[Any]:
    """Convert a Scala Snowpark DataFrame to a pandas DataFrame for grid display.

    Extracts the SQL plan from the named Scala variable (via a Java
    System property to avoid string-escaping issues), creates a Python
    Snowpark DataFrame with a server-side LIMIT, and calls ``.to_pandas()``.

    Returns ``None`` (with a warning) if any step fails so the caller
    can fall back to text output.
    """
    import jpype

    session = _scala_state.get("python_session")
    if session is None:
        print(
            "Warning: No Python Snowpark session stored — cannot render "
            "grid viewer. Call inject_session_credentials(session) first.",
            file=sys.stderr,
        )
        return None

    # Extract the SQL plan from the Scala DF into a System property
    prop_key = f"_grid_sql_{var_name}"
    for extract_expr in [
        f"{var_name}.queries.last",
        f"{var_name}.queries.apply({var_name}.queries.length - 1)",
    ]:
        code = f'System.setProperty("{prop_key}", {extract_expr})'
        success, _, _ = execute_scala(code)
        if success:
            System = jpype.JClass("java.lang.System")
            sql = str(System.getProperty(prop_key))
            System.clearProperty(prop_key)
            if sql and sql != "null":
                try:
                    return session.sql(sql).limit(limit).to_pandas()
                except Exception:
                    continue

    # Fallback: materialise via a view
    view_name = f"_GRID_{var_name.upper()}"
    code = f'{var_name}.createOrReplaceView("{view_name}")'
    success, _, errors = execute_scala(code)
    if success:
        _interop_views.append(view_name)
        try:
            return session.table(view_name).limit(limit).to_pandas()
        except Exception as exc:
            print(
                f"Warning: grid view fallback failed for '{var_name}': {exc}",
                file=sys.stderr,
            )
            return None

    print(
        f"Warning: Could not extract grid data from Scala DataFrame "
        f"'{var_name}'" + (f": {errors}" if errors else ""),
        file=sys.stderr,
    )
    return None


def _register_scala_magic() -> None:
    """Register the ``%%scala`` cell magic and ``%scala`` line magic."""
    try:
        get_ipython()  # type: ignore[name-defined]  # noqa: F841
    except NameError:
        raise RuntimeError("Not in an IPython environment")

    from IPython.core.magic import register_cell_magic, register_line_magic

    @register_cell_magic
    def scala(line, cell):
        """
        Execute Scala code in the embedded JVM interpreter.

        Usage:
            %%scala
            val x = 42
            println(s"The answer is $x")

        Flags (on the %%scala line):
            -i var1,var2        Push Python variables into Scala (auto-detect DF type)
            -i:spark var        Force push as Spark DataFrame (cross-API for Snowpark DFs)
            -i:snowpark var     Force push as Snowpark DataFrame (default for Snowpark DFs)
            -o var1,var2        Pull Scala variables into Python (auto-detect DF type)
            -o:spark var        Force pull as PySpark DataFrame
            -o:snowpark var     Force pull as Snowpark DataFrame (cross-API for Spark DFs)
            --silent            Suppress REPL variable-binding output
            --time              Print execution time
            --text              Suppress grid viewer (text output only)
            --df varname        Explicitly pick which Scala variable to grid-display
            --grid-limit N      Max rows for grid display (default: 1000)

        When the last expression in the cell evaluates to a Snowpark
        DataFrame, the magic automatically converts it to a pandas
        DataFrame (with a server-side LIMIT) and returns it as the
        cell result so Workspace Notebooks render the interactive grid
        viewer.  Use ``--text`` to opt out.

        Examples:
            %%scala -i df_name -o result --time
            val result = df_name.filter(col("id") > 10)

            %%scala -i:spark snowpark_df -o:snowpark spark_result
            val spark_result = snowpark_df.groupBy("region").count()

            %%scala --df my_df
            val my_df = sfSession.table("customers")
            println(s"rows: ${my_df.count()}")
        """
        import time

        args = _parse_magic_args(line)

        if args["inputs"]:
            _push_inputs(args["inputs"])

        t0 = time.time()
        success, output, errors = execute_scala(cell)
        elapsed = time.time() - t0

        if output and not args["silent"]:
            print(output)

        if args["show_time"]:
            print(f"\n[Scala cell executed in {elapsed:.2f}s]")

        if not success:
            msg = errors or "Scala execution failed (no error details captured)"
            print(msg, file=sys.stderr)
            raise MagicExecutionError(msg)

        if errors:
            print(errors, file=sys.stderr)

        if args["outputs"]:
            _pull_outputs(args["outputs"])

        # --- Grid display post-processing ---
        # Skip when -o is used (user is pulling to Python explicitly),
        # or when --text is set.
        has_outputs = bool(args["outputs"])
        grid_mode = None
        if not has_outputs and not args["text_only"]:
            if args["df_varname"]:
                grid_mode = "named"
            else:
                grid_mode = "auto"

        pdf = None
        if grid_mode == "auto" and output:
            df_var = _detect_last_scala_df(output)
            if df_var:
                pdf = _scala_df_to_pandas(
                    df_var, limit=args["grid_limit"])
        elif grid_mode == "named":
            varname = args["df_varname"]
            if _is_snowpark_scala_df(varname):
                pdf = _scala_df_to_pandas(
                    varname, limit=args["grid_limit"])
            else:
                print(
                    f"Warning: Scala variable '{varname}' is not a "
                    "Snowpark DataFrame; skipping grid display.",
                    file=sys.stderr,
                )

        if pdf is not None:
            return pdf

    @register_line_magic
    def scala(line):  # noqa: F811 — intentional re-use of name for %scala
        """
        Execute a single line of Scala code.

        Usage:
            %scala println("Hello from Scala!")
            %scala val x = 42
        """
        if not line.strip():
            print("Usage: %scala <scala expression>", file=sys.stderr)
            return

        success, output, errors = execute_scala(line)
        if output:
            print(output)
        if not success:
            msg = errors or "Scala execution failed (no error details captured)"
            print(msg, file=sys.stderr)
            raise MagicExecutionError(msg)
        if errors:
            print(errors, file=sys.stderr)

    _scala_state["magic_registered"] = True


# =============================================================================
# %%java IPython Magic
# =============================================================================

def _push_inputs_java(items: List[Tuple[str, Optional[str]]]) -> None:
    """Push Python variables into JShell.

    Dispatch is similar to the Scala version but targets the Java
    Snowpark API (``com.snowflake.snowpark_java.DataFrame``).
    """
    ip = get_ipython()  # type: ignore[name-defined]  # noqa: F821
    for name, hint in items:
        if name not in ip.user_ns:
            print(
                f"Warning: Python variable '{name}' not found, skipping",
                file=sys.stderr,
            )
            continue

        value = ip.user_ns[name]

        if _is_snowpark_python_df(value):
            _push_snowpark_df_to_java(name, value)
        elif isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            execute_java(f'String {name} = "{escaped}";')
        elif isinstance(value, bool):
            execute_java(f"boolean {name} = {'true' if value else 'false'};")
        elif isinstance(value, int):
            execute_java(f"long {name} = {value}L;")
        elif isinstance(value, float):
            execute_java(f"double {name} = {value};")
        else:
            print(
                f"Warning: Cannot push '{name}' (type {type(value).__name__}) to Java",
                file=sys.stderr,
            )


def _pull_outputs_java(items: List[Tuple[str, Optional[str]]]) -> None:
    """Pull Java variables back into Python."""
    ip = get_ipython()  # type: ignore[name-defined]  # noqa: F821
    for name, hint in items:
        if _is_snowpark_java_df_by_name(name):
            py_df = _pull_snowpark_java_df(name)
            if py_df is not None:
                ip.user_ns[name] = py_df
                continue

        val = _pull_from_jshell(name)
        if val is None:
            print(f"Warning: Java variable '{name}' not found, skipping",
                  file=sys.stderr)
        else:
            ip.user_ns[name] = val


def _push_snowpark_df_to_java(name: str, df) -> bool:
    """Push a Snowpark Python DataFrame into JShell via SQL plan transfer."""
    import jpype

    try:
        queries = df.queries.get("queries", [])
        if not queries:
            print(f"Warning: DataFrame '{name}' has no SQL queries",
                  file=sys.stderr)
            return False
        sql = queries[-1]
    except (AttributeError, KeyError, IndexError) as e:
        print(f"Warning: Could not extract SQL plan from '{name}': {e}",
              file=sys.stderr)
        return False

    prop_key = f"_interop_java_sql_{name}"
    System = jpype.JClass("java.lang.System")
    System.setProperty(prop_key, sql)

    code = (
        f'com.snowflake.snowpark_java.DataFrame {name} = '
        f'javaSession.sql(System.getProperty("{prop_key}"));'
    )
    success, output, errors = execute_java(code)

    System.clearProperty(prop_key)

    if not success:
        msg = errors or output
        print(f"Warning: Failed to create Java DataFrame '{name}': {msg}",
              file=sys.stderr)
    return success


def _is_snowpark_java_df_by_name(name: str) -> bool:
    """Check whether *name* in JShell references a Snowpark Java DataFrame.

    Uses ``instanceof`` via JShell eval for robustness — works regardless
    of whether the user imported with a wildcard or used the fully qualified
    class name.
    """
    jshell = _java_state.get("jshell")
    if jshell is None:
        return False
    try:
        check_code = f"{name} instanceof com.snowflake.snowpark_java.DataFrame"
        events = jshell.eval(check_code)
        for event in events:
            val = event.value()
            if val is not None and str(val).strip() == "true":
                return True
        return False
    except Exception:
        return False


def _pull_snowpark_java_df(name: str) -> Optional[Any]:
    """Pull a Snowpark Java DataFrame back into Python as a Snowpark Python DF.

    Materialises the Java DF to a Snowflake transient table and reads it
    back via the Python Snowpark session. A transient table is used (not a
    temp view) because the Java and Python sessions are separate Snowflake
    connections — temp views are only visible within their owning session.
    """
    table_name = f"_INTEROP_JAVA_{name.upper()}"
    code = f'{name}.write().mode(com.snowflake.snowpark_java.SaveMode.Overwrite).saveAsTable("{table_name}");'
    success, _, errors = execute_java(code)
    if not success:
        print(
            f"Warning: Could not materialise Java DF '{name}' to table"
            + (f": {errors}" if errors else ""),
            file=sys.stderr,
        )
        return None

    _interop_views.append(table_name)

    session = _scala_state.get("python_session")
    if session is None:
        print("Warning: No Python Snowpark session available", file=sys.stderr)
        return None
    try:
        return session.table(table_name)
    except Exception as e:
        print(f"Warning: Could not read interop table '{table_name}': {e}",
              file=sys.stderr)
        return None


def _pull_from_jshell(name: str) -> Optional[Any]:
    """Extract a primitive/string variable from JShell."""
    jshell = _java_state.get("jshell")
    if jshell is None:
        return None
    try:
        import jpype
        VarSnippet = jpype.JClass("jdk.jshell.VarSnippet")
        Snippet = jpype.JClass("jdk.jshell.Snippet")
        snippets = jshell.snippets().iterator()
        target = None
        while snippets.hasNext():
            s = snippets.next()
            if isinstance(s, VarSnippet) and str(s.name()) == name:
                if jshell.status(s) == Snippet.Status.VALID:
                    target = s
        if target is None:
            return None
        val_str = str(jshell.varValue(target))
        type_name = str(target.typeName())
        if type_name in ("int", "long", "short", "byte",
                         "Integer", "Long", "Short", "Byte"):
            return int(val_str)
        elif type_name in ("double", "float", "Double", "Float"):
            return float(val_str)
        elif type_name in ("boolean", "Boolean"):
            return val_str.lower() == "true"
        elif type_name == "String":
            if val_str.startswith('"') and val_str.endswith('"'):
                return val_str[1:-1]
            return val_str
        return val_str
    except Exception:
        return None


def _detect_last_java_df() -> Optional[str]:
    """Detect whether the last JShell variable is a Snowpark Java DataFrame.

    Iterates through JShell's snippet history in reverse, looking for
    the most recent valid ``VarSnippet`` whose ``typeName()`` matches
    the Snowpark Java DataFrame class.

    Returns the variable name or ``None``.
    """
    import jpype

    jshell = _java_state.get("jshell")
    if jshell is None:
        return None

    try:
        VarSnippet = jpype.JClass("jdk.jshell.VarSnippet")
        Snippet = jpype.JClass("jdk.jshell.Snippet")

        candidates = []
        it = jshell.snippets().iterator()
        while it.hasNext():
            s = it.next()
            if isinstance(s, VarSnippet):
                if jshell.status(s) == Snippet.Status.VALID:
                    candidates.append(s)

        for snippet in reversed(candidates):
            type_name = str(snippet.typeName())
            if "DataFrame" in type_name and "snowpark" in type_name:
                return str(snippet.name())
        return None
    except Exception:
        return None


def _java_df_to_pandas(
    var_name: str,
    limit: int = 1000,
) -> Optional[Any]:
    """Convert a Snowpark Java DataFrame to a pandas DataFrame for grid display.

    Attempts SQL plan extraction first (lightweight — no data
    materialisation).  Falls back to writing a limited result set to
    a transient table and reading it from the Python session.

    Returns ``None`` (with a warning) if all strategies fail.
    """
    import jpype

    session = _scala_state.get("python_session")
    if session is None:
        print(
            "Warning: No Python Snowpark session stored — cannot render "
            "grid viewer. Call inject_session_credentials(session) first.",
            file=sys.stderr,
        )
        return None

    # Strategy 1: extract the SQL plan via a Java System property.
    # Snowpark Java's DataFrame.getQueries() returns Map<String, List<String>>.
    prop_key = f"_grid_java_sql_{var_name}"
    extract_code = (
        f'java.util.List<String> _gqs = {var_name}.getQueries().get("queries");\n'
        f'if (_gqs != null && !_gqs.isEmpty()) {{\n'
        f'  System.setProperty("{prop_key}", _gqs.get(_gqs.size() - 1));\n'
        f'}}'
    )
    success, _, _ = execute_java(extract_code)
    if success:
        System = jpype.JClass("java.lang.System")
        sql = str(System.getProperty(prop_key))
        System.clearProperty(prop_key)
        if sql and sql != "null":
            try:
                return session.sql(sql).limit(limit).to_pandas()
            except Exception:
                pass

    # Strategy 2: materialise limited rows to a transient table.
    table_name = f"_GRID_JAVA_{var_name.upper()}"
    mat_code = (
        f'{var_name}.limit({limit}).write()'
        f'.mode(com.snowflake.snowpark_java.SaveMode.Overwrite)'
        f'.saveAsTable("{table_name}");'
    )
    success, _, errors = execute_java(mat_code)
    if success:
        _interop_views.append(table_name)
        try:
            return session.table(table_name).to_pandas()
        except Exception as exc:
            print(
                f"Warning: Java grid fallback failed for '{var_name}': {exc}",
                file=sys.stderr,
            )
            return None

    print(
        f"Warning: Could not extract grid data from Java DataFrame "
        f"'{var_name}'" + (f": {errors}" if errors else ""),
        file=sys.stderr,
    )
    return None


def _register_java_magic() -> None:
    """Register the ``%%java`` cell magic and ``%java`` line magic."""
    try:
        get_ipython()  # type: ignore[name-defined]  # noqa: F841
    except NameError:
        raise RuntimeError("Not in an IPython environment")

    from IPython.core.magic import register_cell_magic, register_line_magic

    @register_cell_magic
    def java(line, cell):
        """
        Execute Java code in the embedded JShell interpreter.

        Usage:
            %%java
            System.out.println("Hello from Java!");

        Flags (on the %%java line):
            -i var1,var2        Push Python variables into Java
            -i:snowpark var     Push as Snowpark Java DataFrame
            -o var1,var2        Pull Java variables into Python
            -o:snowpark var     Pull as Snowpark Python DataFrame
            --silent            Suppress output
            --time              Print execution time
            --text              Suppress grid viewer (text output only)
            --df varname        Explicitly pick which Java variable to grid-display
            --grid-limit N      Max rows for grid display (default: 1000)

        When the last variable defined in the cell is a Snowpark Java
        DataFrame, the magic automatically converts it to a pandas
        DataFrame and returns it as the cell result so Workspace
        Notebooks render the interactive grid viewer.  Use ``--text``
        to opt out.
        """
        import time

        args = _parse_magic_args(line)

        if args["inputs"]:
            _push_inputs_java(args["inputs"])

        t0 = time.time()
        success, output, errors = execute_java(cell)
        elapsed = time.time() - t0

        if output and not args["silent"]:
            print(output)

        if args["show_time"]:
            print(f"\n[Java cell executed in {elapsed:.2f}s]")

        if not success:
            msg = errors or "Java execution failed (no error details captured)"
            print(msg, file=sys.stderr)
            raise MagicExecutionError(msg)

        if errors:
            print(errors, file=sys.stderr)

        if args["outputs"]:
            _pull_outputs_java(args["outputs"])

        # --- Grid display post-processing ---
        has_outputs = bool(args["outputs"])
        grid_mode = None
        if not has_outputs and not args["text_only"]:
            if args["df_varname"]:
                grid_mode = "named"
            else:
                grid_mode = "auto"

        pdf = None
        if grid_mode == "auto":
            df_var = _detect_last_java_df()
            if df_var:
                pdf = _java_df_to_pandas(
                    df_var, limit=args["grid_limit"])
        elif grid_mode == "named":
            varname = args["df_varname"]
            if _is_snowpark_java_df_by_name(varname):
                pdf = _java_df_to_pandas(
                    varname, limit=args["grid_limit"])
            else:
                print(
                    f"Warning: Java variable '{varname}' is not a "
                    "Snowpark Java DataFrame; skipping grid display.",
                    file=sys.stderr,
                )

        if pdf is not None:
            return pdf

    @register_line_magic
    def java(line):  # noqa: F811
        """
        Execute a single line of Java code.

        Usage:
            %java System.out.println("Hello!");
        """
        if not line.strip():
            print("Usage: %java <java expression>", file=sys.stderr)
            return

        success, output, errors = execute_java(line)
        if output:
            print(output)
        if not success:
            msg = errors or "Java execution failed (no error details captured)"
            print(msg, file=sys.stderr)
            raise MagicExecutionError(msg)
        if errors:
            print(errors, file=sys.stderr)

    _java_state["magic_registered"] = True


# =============================================================================
# Java UDF Registration (%%java_udf / %register_java_udf)
# =============================================================================

def _get_jshell_class_source(class_name: str) -> Optional[str]:
    """Extract a class's source from JShell's snippet history.

    JShell stores every evaluated snippet.  This walks the history
    looking for ``TypeDeclSnippet`` entries whose name matches
    *class_name* and returns the source of the **last** matching
    definition (so redefinitions are handled correctly).
    """
    import jpype

    jshell = _java_state.get("jshell")
    if jshell is None:
        return None

    TypeDeclSnippet = jpype.JClass(
        "jdk.jshell.TypeDeclSnippet"
    )
    it = jshell.snippets().iterator()
    source = None
    while it.hasNext():
        snippet = it.next()
        if isinstance(snippet, TypeDeclSnippet):
            if str(snippet.name()) == class_name:
                source = str(snippet.source())
    return source


def _parse_udf_args_string(args_str: str):
    """Parse ``'x INT, y VARCHAR'`` into list of (name, type) tuples."""
    result = []
    for part in args_str.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split(None, 1)
        if len(tokens) == 2:
            result.append((tokens[0], tokens[1]))
        else:
            result.append((tokens[0], tokens[0]))
    return result


def _register_java_udf_impl(
    name: str,
    args_str: str,
    returns: str,
    handler: str,
    body: str,
    *,
    temporary: bool = True,
    replace: bool = True,
) -> Tuple[bool, str]:
    """Register a Java UDF via javaSession in JShell (preferred) or Python.

    Primary path: executes ``CREATE FUNCTION`` through the JShell
    ``javaSession`` so that temporary UDFs are visible when called
    from ``%%java`` cells (temporary objects are session-scoped and
    the Java session uses a separate JDBC connection).

    Fallback: uses the Python Snowpark session (only reliable for
    permanent UDFs that are visible across sessions).

    Args:
        name: UDF name in Snowflake
        args_str: Argument list, e.g. ``"x INT, y VARCHAR"``
        returns: Return type, e.g. ``"INT"``
        handler: ``ClassName.methodName``
        body: Java handler source code
        temporary: Create as TEMPORARY (default True)
        replace: Use OR REPLACE (default True)

    Returns:
        (success, message)
    """
    kind = "TEMPORARY " if temporary else ""
    or_replace = "OR REPLACE " if replace else ""
    create_sql = (
        f"CREATE {or_replace}{kind}"
        f"FUNCTION {name}({args_str}) "
        f"RETURNS {returns} "
        f"LANGUAGE JAVA "
        f"RUNTIME_VERSION = '17' "
        f"HANDLER = '{handler}' "
        f"AS $$ {body} $$"
    )

    # --- Primary path: execute via javaSession in JShell ---
    jshell = _java_state.get("jshell")
    if jshell is not None:
        escaped = (
            create_sql
            .replace('\\', '\\\\')
            .replace('"', '\\"')
            .replace('\n', '\\n')
            .replace('\r', '')
        )
        java_code = f'javaSession.sql("{escaped}").collect();'
        success, output, errors = _execute_jshell(java_code)
        if success:
            return True, f"UDF '{name}' registered via javaSession"
        return False, f"UDF registration failed in javaSession: {errors}"

    # --- Fallback: Python session (works for permanent UDFs) ---
    from snowflake.snowpark.context import get_active_session
    try:
        session = get_active_session()
    except Exception:
        return False, "No active Snowpark or Java session found."

    try:
        session.sql(create_sql).collect()
        note = ""
        if temporary:
            note = (
                " (Warning: created in Python session — temporary UDFs "
                "may not be visible from javaSession)"
            )
        return True, f"UDF '{name}' registered via Python session{note}"
    except Exception as e:
        return False, f"UDF registration failed: {e}"


def _register_java_udf_magics() -> None:
    """Register ``%%java_udf`` and ``%register_java_udf`` magics."""
    try:
        get_ipython()  # type: ignore[name-defined]
    except NameError:
        return

    import argparse
    import shlex
    from IPython.core.magic import (
        register_cell_magic,
        register_line_magic,
    )

    def _build_parser():
        p = argparse.ArgumentParser(prog="java_udf")
        p.add_argument("--name", required=True)
        p.add_argument("--args", required=True)
        p.add_argument("--returns", required=True)
        p.add_argument("--handler", required=True)
        p.add_argument(
            "--permanent", action="store_true",
            default=False,
        )
        return p

    @register_cell_magic
    def java_udf(line, cell):
        """Execute Java code in JShell then register a UDF.

        Compiles and runs the cell in JShell (so you can test
        locally), then extracts the handler class source from
        JShell's snippet history and registers it as a UDF.

        Usage::

            %%java_udf --name double_it --args "x INT" \\
                       --returns INT --handler Handler.compute
            class Handler {
                public static int compute(int x) {
                    return x * 2;
                }
            }
            System.out.println(Handler.compute(21));
        """
        import time

        parser = _build_parser()
        try:
            opts = parser.parse_args(shlex.split(line))
        except SystemExit:
            print(
                "Usage: %%java_udf --name NAME "
                '--args "ARGS" --returns TYPE '
                "--handler Class.method",
                file=sys.stderr,
            )
            return

        # Step 1: Execute in JShell
        t0 = time.time()
        success, output, errors = execute_java(cell)
        elapsed = time.time() - t0

        if output:
            print(output)
        print(f"[JShell executed in {elapsed:.2f}s]")

        if not success:
            msg = (
                errors
                or "Java execution failed"
            )
            print(msg, file=sys.stderr)
            raise MagicExecutionError(msg)
        if errors:
            print(errors, file=sys.stderr)

        # Step 2: Extract handler class source
        handler_class = opts.handler.split(".")[0]
        source = _get_jshell_class_source(handler_class)
        if not source:
            raise MagicExecutionError(
                f"Class '{handler_class}' not found in "
                "JShell snippet history"
            )

        # Step 3: Register
        ok, msg = _register_java_udf_impl(
            name=opts.name,
            args_str=opts.args,
            returns=opts.returns,
            handler=opts.handler,
            body=source,
            temporary=not opts.permanent,
        )
        if ok:
            print(msg)
        else:
            raise MagicExecutionError(msg)

    @register_line_magic
    def register_java_udf(line):
        """Register a previously-defined JShell class as a UDF.

        Usage::

            %register_java_udf --name double_it --args "x INT" \\
                               --returns INT --handler Handler.compute
        """
        parser = _build_parser()
        try:
            opts = parser.parse_args(shlex.split(line))
        except SystemExit:
            print(
                "Usage: %register_java_udf --name NAME "
                '--args "ARGS" --returns TYPE '
                "--handler Class.method",
                file=sys.stderr,
            )
            return

        handler_class = opts.handler.split(".")[0]
        source = _get_jshell_class_source(handler_class)
        if not source:
            raise MagicExecutionError(
                f"Class '{handler_class}' not found in "
                "JShell snippet history. Define it in a "
                "%%java cell first."
            )

        ok, msg = _register_java_udf_impl(
            name=opts.name,
            args_str=opts.args,
            returns=opts.returns,
            handler=opts.handler,
            body=source,
            temporary=not opts.permanent,
        )
        if ok:
            print(msg)
        else:
            raise MagicExecutionError(msg)

    _java_state["udf_magic_registered"] = True


# =============================================================================
# Snowpark Session Management
# =============================================================================

def inject_session_credentials(session) -> Dict[str, str]:
    """
    Extract credentials from a Python Snowpark session and make them
    available to Scala code via Java System properties.

    Java's System.getenv() caches the process environment at JVM
    startup, so os.environ changes made after jpype.startJVM() are
    invisible to Scala's sys.env(). We therefore also set Java System
    properties via System.setProperty(), which Scala reads as
    sys.props("key").

    Args:
        session: Active Snowpark Python session

    Returns:
        Dict of credential names to values that were set
    """
    _scala_state["python_session"] = session

    creds = {}

    def _safe_get(fn, strip_quotes=True):
        try:
            val = fn()
            if strip_quotes and val:
                val = val.strip('"')
            return val or ""
        except Exception:
            return ""

    creds["SNOWFLAKE_ACCOUNT"] = _safe_get(
        lambda: session.sql(
            "SELECT CURRENT_ACCOUNT()"
        ).collect()[0][0]
    )
    creds["SNOWFLAKE_USER"] = _safe_get(
        lambda: session.sql(
            "SELECT CURRENT_USER()"
        ).collect()[0][0]
    )
    creds["SNOWFLAKE_ROLE"] = _safe_get(
        session.get_current_role
    )
    creds["SNOWFLAKE_DATABASE"] = _safe_get(
        session.get_current_database
    )
    creds["SNOWFLAKE_SCHEMA"] = _safe_get(
        session.get_current_schema
    )
    creds["SNOWFLAKE_WAREHOUSE"] = _safe_get(
        session.get_current_warehouse
    )

    account = creds["SNOWFLAKE_ACCOUNT"]
    if account:
        host = os.environ.get("SNOWFLAKE_HOST", "")
        if host:
            creds["SNOWFLAKE_URL"] = f"https://{host}"
        else:
            creds["SNOWFLAKE_URL"] = (
                f"https://{account}.snowflakecomputing.com"
            )

    # Authentication token: inside SPCS (Workspace Notebooks) we must
    # use the container's OAuth token; PAT is blocked from inside SPCS.
    spcs_token_path = "/snowflake/session/token"
    if os.path.isfile(spcs_token_path):
        with open(spcs_token_path) as f:
            creds["SNOWFLAKE_TOKEN"] = f.read().strip()
        creds["SNOWFLAKE_AUTH_TYPE"] = "oauth"
    else:
        pat = os.environ.get("SNOWFLAKE_PAT", "")
        if pat:
            creds["SNOWFLAKE_TOKEN"] = pat
            creds["SNOWFLAKE_AUTH_TYPE"] = (
                "programmatic_access_token"
            )

    # Set in Python os.environ (for Python-side reads)
    for key, val in creds.items():
        if val:
            os.environ[key] = val

    # Set as Java System properties so Scala can read them
    # via sys.props("SNOWFLAKE_URL") etc., bypassing the
    # cached System.getenv() map.
    try:
        import jpype
        if jpype.isJVMStarted():
            System = jpype.JClass("java.lang.System")
            for key, val in creds.items():
                if val:
                    System.setProperty(key, val)
    except Exception:
        pass

    return creds


def create_snowpark_scala_session_code() -> str:
    """
    Generate Scala code to create a Snowpark session using credentials
    from Java System properties (set by inject_session_credentials).

    Authentication is determined automatically:
    - Inside SPCS/Workspace: uses the container's OAuth token
    - External: uses PAT with programmatic_access_token authenticator

    Returns:
        Scala code string ready for %%scala execution
    """
    auth_config = (
        '  "TOKEN"         -> prop("SNOWFLAKE_TOKEN"),\n'
        '  "AUTHENTICATOR" -> prop("SNOWFLAKE_AUTH_TYPE")'
    )

    return (
        "import com.snowflake.snowpark._\n"
        "import com.snowflake.snowpark.functions._\n"
        "\n"
        "def prop(k: String): String = {\n"
        "  val v = System.getProperty(k)\n"
        "  require(v != null, "
        's"Java System property \'$k\' not set. '
        "Run inject_session_credentials(session) first.\")\n"
        "  v\n"
        "}\n"
        "\n"
        "val sfSession = Session.builder.configs(Map(\n"
        '  "URL"       -> prop("SNOWFLAKE_URL"),\n'
        '  "USER"      -> prop("SNOWFLAKE_USER"),\n'
        '  "ROLE"      -> prop("SNOWFLAKE_ROLE"),\n'
        '  "DB"        -> prop("SNOWFLAKE_DATABASE"),\n'
        '  "SCHEMA"    -> prop("SNOWFLAKE_SCHEMA"),\n'
        '  "WAREHOUSE" -> prop("SNOWFLAKE_WAREHOUSE"),\n'
        f"  {auth_config}\n"
        ")).create\n"
        "\n"
        'println("Snowpark Scala session created")\n'
        "val _user = sfSession.sql("
        '"SELECT CURRENT_USER()")'
        ".collect()(0).getString(0)\n"
        "val _role = sfSession.sql("
        '"SELECT CURRENT_ROLE()")'
        ".collect()(0).getString(0)\n"
        "val _db = sfSession.sql("
        '"SELECT CURRENT_DATABASE()")'
        ".collect()(0).getString(0)\n"
        'println(s"  User:      ${_user}")\n'
        'println(s"  Role:      ${_role}")\n'
        'println(s"  Database:  ${_db}")\n'
    )


def create_snowpark_java_session_code() -> str:
    """Generate Java code to create a Snowpark session using credentials
    from Java System properties (set by ``inject_session_credentials``).

    Returns:
        Java code string ready for ``%%java`` execution.
    """
    return (
        "import com.snowflake.snowpark_java.*;\n"
        "import java.util.HashMap;\n"
        "import java.util.Map;\n"
        "\n"
        "Map<String, String> props = new HashMap<>();\n"
        'props.put("URL",       System.getProperty("SNOWFLAKE_URL"));\n'
        'props.put("USER",      System.getProperty("SNOWFLAKE_USER"));\n'
        'props.put("ROLE",      System.getProperty("SNOWFLAKE_ROLE"));\n'
        'props.put("DB",        System.getProperty("SNOWFLAKE_DATABASE"));\n'
        'props.put("SCHEMA",    System.getProperty("SNOWFLAKE_SCHEMA"));\n'
        'props.put("WAREHOUSE", System.getProperty("SNOWFLAKE_WAREHOUSE"));\n'
        'props.put("TOKEN",     System.getProperty("SNOWFLAKE_TOKEN"));\n'
        'props.put("AUTHENTICATOR", System.getProperty("SNOWFLAKE_AUTH_TYPE"));\n'
        "\n"
        "Session javaSession = Session.builder().configs(props).create();\n"
        "\n"
        'System.out.println("Snowpark Java session created");\n'
        'Row[] info = javaSession.sql("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_DATABASE()").collect();\n'
        'System.out.println("  User:      " + info[0].getString(0));\n'
        'System.out.println("  Role:      " + info[0].getString(1));\n'
        'System.out.println("  Database:  " + info[0].getString(2));\n'
    )


def bootstrap_snowpark_java(session) -> Tuple[bool, str]:
    """All-in-one: inject credentials and create a Snowpark Java session
    in JShell.

    Args:
        session: Active Snowpark Python session

    Returns:
        (success, message)
    """
    creds = inject_session_credentials(session)
    if not creds.get("SNOWFLAKE_ACCOUNT"):
        return False, "Failed to extract account from session"

    if not creds.get("SNOWFLAKE_TOKEN"):
        return False, (
            "No auth token available. Inside a Workspace the "
            "SPCS token at /snowflake/session/token should be "
            "auto-detected. For external use, set "
            "os.environ['SNOWFLAKE_PAT'] before calling this."
        )

    code = create_snowpark_java_session_code()
    success, output, errors = execute_java(code)

    if success:
        return True, output
    else:
        return False, errors or output or "Java session creation failed"


# =============================================================================
# Spark Connect (opt-in)
# =============================================================================

def setup_spark_connect(
    session=None,
    port: int = 15002,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Create a Scala SparkSession connected to the local Spark Connect server.

    The server should already be running (started during
    setup_scala_environment() when spark_connect.enabled is true). If not,
    this function will attempt to start it, though that requires the JVM to
    not yet be running.

    Args:
        session: Active Snowpark Python session (for auth, only needed if
                 server is not already running)
        port: gRPC server port (default: 15002)
        timeout: Seconds to wait for server startup

    Returns:
        Dict with setup status and details
    """
    import socket

    result = {
        "success": False,
        "server_port": port,
        "pyspark_version": None,
        "scala_spark_session": False,
        "errors": [],
    }

    def _port_open():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (socket.error, OSError):
            return False

    if not _port_open():
        result["errors"].append(
            "Spark Connect server is not running. "
            "Ensure spark_connect.enabled: true in scala_packages.yaml "
            "and re-run setup_scala_environment.sh, then "
            "setup_scala_environment() (which starts the server)."
        )
        return result

    try:
        import pyspark
        result["pyspark_version"] = pyspark.__version__
    except ImportError:
        pass

    # Create a Scala SparkSession connected to the local server
    import jpype
    if not jpype.isJVMStarted():
        result["errors"].append("JVM not started — call setup_scala_environment() first")
        return result

    spark_code = (
        "import org.apache.spark.sql.{SparkSession => SparkS}\n"
        f'val spark = SparkS.builder().remote("sc://localhost:{port}")'
        '.config("spark.sql.session.timeZone", "UTC").getOrCreate()\n'
        'println("Spark Connect session ready: spark")'
    )
    success, output, errors = execute_scala(spark_code)
    if success:
        result["scala_spark_session"] = True
        if output:
            print(output)
    else:
        result["errors"].append(
            f"Failed to create Scala SparkSession: {errors or output}"
        )
        return result

    # Create a Python-side PySpark SparkSession for DataFrame interop.
    # This lets -i/-o auto-transfer PySpark DataFrames via temp views.
    try:
        from pyspark.sql import SparkSession as PySparkSession
        spark_py = (
            PySparkSession.builder
            .remote(f"sc://localhost:{port}")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        _scala_state["pyspark_session"] = spark_py
        result["pyspark_session"] = True
    except Exception as e:
        result["pyspark_session"] = False
        result["errors"].append(f"PySpark session creation failed: {e}")

    result["success"] = len(result["errors"]) == 0
    return result


# =============================================================================
# Variable Transfer: Python <-> Scala
# =============================================================================

def push_to_scala(name: str, value: Any, type_hint: str = "Any") -> bool:
    """
    Bind a Python value into the Scala interpreter's namespace.

    Supports primitive types, strings, and Java-compatible objects via JPype.

    Args:
        name: Variable name in Scala
        value: Python value (auto-converted by JPype for primitives/strings)
        type_hint: Scala type hint (default: "Any")

    Returns:
        True if successful
    """
    itype = _scala_state.get("interpreter_type")
    if itype not in ("imain", "ammonite-lite"):
        return False

    interpreter = _scala_state["interpreter"]
    try:
        interpreter.bind(name, type_hint, value)
        return True
    except Exception:
        # Fallback: define via code execution
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            success, _, _ = execute_scala(f'val {name}: String = "{escaped}"')
            return success
        elif isinstance(value, (int, float, bool)):
            success, _, _ = execute_scala(f"val {name} = {value}")
            return success
        return False


def pull_from_scala(name: str) -> Any:
    """
    Extract a value from the Scala interpreter's namespace.

    Returns the value (JPype auto-converts primitives/strings) or None if
    the variable does not exist.
    """
    itype = _scala_state.get("interpreter_type")
    if itype not in ("imain", "ammonite-lite"):
        return None

    interpreter = _scala_state["interpreter"]
    try:
        opt = interpreter.valueOfTerm(name)
        if opt.isDefined():
            return opt.get()
        return None
    except Exception:
        return None


# =============================================================================
# DataFrame Interop: Python <-> Scala (Snowpark + Spark Connect)
# =============================================================================
#
# The -i / -o flags on %%scala auto-detect DataFrame types and pick the
# right transfer strategy:
#
#   Snowpark Python DF  → sfSession.sql(sql_plan)  → Snowpark Scala DF
#   PySpark DF          → temp view → spark.table() → Spark Scala DF
#   Snowpark Scala DF   → sql plan  → session.sql() → Snowpark Python DF
#   Spark Scala DF      → temp view → spark_py.table() → PySpark DF
#
# Cross-API transfers are available via type hints (-i:spark, -o:snowpark):
#   Snowpark Python DF  -i:spark→  spark.sql(sql_plan)  → Spark Scala DF
#   Spark Scala DF      -o:snowpark→  transient table   → Snowpark Python DF

_interop_views: List[str] = []


def _is_snowpark_python_df(obj) -> bool:
    """Check if *obj* is a Snowpark Python DataFrame."""
    try:
        from snowflake.snowpark import DataFrame
        return isinstance(obj, DataFrame)
    except ImportError:
        return False


def _is_pyspark_df(obj) -> bool:
    """Check if *obj* is a PySpark DataFrame (classic or Connect variant)."""
    try:
        from pyspark.sql import DataFrame as PySparkDF
        if isinstance(obj, PySparkDF):
            return True
    except ImportError:
        pass
    try:
        from pyspark.sql.connect.dataframe import DataFrame as ConnectDF
        if isinstance(obj, ConnectDF):
            return True
    except ImportError:
        pass
    return False


def _push_snowpark_df(name: str, df) -> bool:
    """
    Push a Snowpark Python DataFrame into Scala by transferring its SQL
    query plan.  No data is materialised — only the SQL string crosses
    the bridge, and Scala creates its own lazy DataFrame from it.

    The SQL is passed through a Java System property to avoid all
    string-escaping issues.
    """
    import jpype

    try:
        queries = df.queries.get("queries", [])
        if not queries:
            print(f"Warning: DataFrame '{name}' has no SQL queries",
                  file=sys.stderr)
            return False
        sql = queries[-1]
    except (AttributeError, KeyError, IndexError) as e:
        print(f"Warning: Could not extract SQL plan from '{name}': {e}",
              file=sys.stderr)
        return False

    prop_key = f"_interop_sql_{name}"
    System = jpype.JClass("java.lang.System")
    System.setProperty(prop_key, sql)

    code = f'val {name} = sfSession.sql(System.getProperty("{prop_key}"))'
    success, output, errors = execute_scala(code)

    System.clearProperty(prop_key)

    if not success:
        msg = errors or output
        print(f"Warning: Failed to create Scala DataFrame '{name}': {msg}",
              file=sys.stderr)
    return success


def _push_snowpark_df_as_spark(name: str, df) -> bool:
    """Push a Snowpark Python DataFrame into Scala as a **Spark** DataFrame.

    Cross-API transfer: extracts the SQL plan from the Snowpark DF and
    executes it via ``spark.sql()`` in Scala (through the Spark Connect
    gRPC proxy).  The proxy forwards Snowflake SQL, so the same query
    plan works with both ``sfSession.sql()`` and ``spark.sql()``.
    """
    import jpype

    try:
        queries = df.queries.get("queries", [])
        if not queries:
            print(f"Warning: DataFrame '{name}' has no SQL queries",
                  file=sys.stderr)
            return False
        sql = queries[-1]
    except (AttributeError, KeyError, IndexError) as e:
        print(f"Warning: Could not extract SQL plan from '{name}': {e}",
              file=sys.stderr)
        return False

    prop_key = f"_interop_sql_{name}"
    System = jpype.JClass("java.lang.System")
    System.setProperty(prop_key, sql)

    code = f'val {name} = spark.sql(System.getProperty("{prop_key}"))'
    success, output, errors = execute_scala(code)

    System.clearProperty(prop_key)

    if not success:
        msg = errors or output
        print(f"Warning: Failed to create Scala Spark DataFrame '{name}': {msg}",
              file=sys.stderr)
    return success


_spark_interop_views: List[str] = []


def _push_pyspark_df(name: str, df) -> bool:
    """Push a PySpark DataFrame into Scala as a Spark DataFrame.

    PySpark and Scala use separate client sessions on the Spark Connect
    server, so temp views aren't shared.  Instead, materialise the DF to
    a Snowflake transient table and read it back in Scala.
    """
    table_name = f"_INTEROP_PYSPARK_{name.upper()}"
    try:
        df.write.mode("overwrite").saveAsTable(table_name)
    except Exception as e:
        print(f"Warning: Failed to write interop table for '{name}': {e}",
              file=sys.stderr)
        return False

    _interop_views.append(table_name)

    code = f'val {name} = spark.table("{table_name}")'
    success, output, errors = execute_scala(code)

    if not success:
        msg = errors or output
        print(f"Warning: Failed to create Scala Spark DataFrame '{name}': {msg}",
              file=sys.stderr)
    return success


def _is_snowpark_scala_df(name: str) -> bool:
    """Check if a Scala variable is a Snowpark DataFrame."""
    code = (
        f"val _isdf_{name}: Boolean = "
        f"try {{ {name}.isInstanceOf[com.snowflake.snowpark.DataFrame] }} "
        f"catch {{ case _: Throwable => false }}"
    )
    success, _, _ = execute_scala(code)
    if not success:
        return False
    val = pull_from_scala(f"_isdf_{name}")
    try:
        return bool(val)
    except Exception:
        return False


def _is_spark_scala_df(name: str) -> bool:
    """Check if a Scala variable is a Spark (Connect) DataFrame."""
    code = (
        f"val _issdf_{name}: Boolean = "
        f"try {{ {name}.isInstanceOf[org.apache.spark.sql.Dataset[_]] }} "
        f"catch {{ case _: Throwable => false }}"
    )
    success, _, _ = execute_scala(code)
    if not success:
        return False
    val = pull_from_scala(f"_issdf_{name}")
    try:
        return bool(val)
    except Exception:
        return False


def _pull_snowpark_df(name: str) -> Optional[Any]:
    """
    Pull a Snowpark Scala DataFrame into Python by extracting the SQL
    query plan.

    Strategies (in order of preference):
      1. Extract SQL via the Scala DataFrame.queries API and transfer
         through a Java System property.
      2. Fall back to creating a non-temporary VIEW and reading it from
         the Python session (the view is tracked for cleanup).
    """
    import jpype

    session = _scala_state.get("python_session")
    if session is None:
        print(
            "Warning: No Python Snowpark session stored. "
            "Call inject_session_credentials(session) first.",
            file=sys.stderr,
        )
        return None

    # Strategy 1 — extract SQL plan via Scala .queries API
    prop_key = f"_interop_sql_{name}"
    for extract_expr in [
        f"{name}.queries.last",
        f"{name}.queries.apply({name}.queries.length - 1)",
    ]:
        code = f'System.setProperty("{prop_key}", {extract_expr})'
        success, _, _ = execute_scala(code)
        if success:
            System = jpype.JClass("java.lang.System")
            sql = str(System.getProperty(prop_key))
            System.clearProperty(prop_key)
            if sql and sql != "null":
                try:
                    return session.sql(sql)
                except Exception:
                    continue

    # Strategy 2 — view-based fallback
    view_name = f"_INTEROP_{name.upper()}"
    code = f'{name}.createOrReplaceView("{view_name}")'
    success, _, errors = execute_scala(code)
    if success:
        _interop_views.append(view_name)
        return session.table(view_name)

    print(
        f"Warning: Could not transfer Scala DataFrame '{name}' to Python"
        + (f": {errors}" if errors else ""),
        file=sys.stderr,
    )
    return None


def _pull_spark_df(name: str) -> Optional[Any]:
    """Pull a Scala Spark DataFrame into Python as a PySpark DataFrame.

    PySpark and Scala use separate client sessions on the Spark Connect
    server, so temp views aren't shared.  Instead, materialise the Scala
    DF to a Snowflake table and read it back from the Python PySpark session.
    """
    spark_py = _scala_state.get("pyspark_session")
    if spark_py is None:
        print(
            "Warning: No PySpark session available. "
            "Call setup_spark_connect() first.",
            file=sys.stderr,
        )
        return None

    table_name = f"_INTEROP_SPARK_{name.upper()}"
    code = (
        f'{name}.write.mode("overwrite")'
        f'.saveAsTable("{table_name}")'
    )
    success, _, errors = execute_scala(code)
    if not success:
        print(
            f"Warning: Could not materialise Spark DF '{name}' to table"
            + (f": {errors}" if errors else ""),
            file=sys.stderr,
        )
        return None

    _interop_views.append(table_name)

    try:
        return spark_py.table(table_name)
    except Exception as e:
        print(f"Warning: Could not read interop table '{table_name}': {e}",
              file=sys.stderr)
        return None


def _pull_spark_df_as_snowpark(name: str) -> Optional[Any]:
    """Pull a Scala Spark DataFrame into Python as a **Snowpark** DataFrame.

    Cross-API transfer: materialises the Spark DF to a Snowflake transient
    table, then reads it from the Snowpark Python session.  The transient
    table is tracked for cleanup.
    """
    session = _scala_state.get("python_session")
    if session is None:
        print(
            "Warning: No Python Snowpark session stored. "
            "Call inject_session_credentials(session) first.",
            file=sys.stderr,
        )
        return None

    table_name = f"_INTEROP_SPARK_{name.upper()}"

    code = (
        f'{name}.write.mode("overwrite")'
        f'.option("type", "transient")'
        f'.saveAsTable("{table_name}")'
    )
    success, _, errors = execute_scala(code)
    if not success:
        print(
            f"Warning: Could not materialise Spark DF '{name}' to table"
            + (f": {errors}" if errors else ""),
            file=sys.stderr,
        )
        return None

    _interop_views.append(table_name)

    try:
        return session.table(table_name)
    except Exception as e:
        print(f"Warning: Could not read interop table '{table_name}': {e}",
              file=sys.stderr)
        return None


def cleanup_interop_views() -> int:
    """
    Drop any tables/views created during DataFrame interop transfers.
    Call this at the end of a notebook.

    Uses the Snowpark session exclusively (not PySpark) because Spark
    Connect clients have isolated sessions and can't see each other's
    objects.

    Returns the number of objects cleaned up.
    """
    from contextlib import suppress

    session = _scala_state.get("python_session")

    dropped = 0

    all_objects = list(dict.fromkeys(
        list(_interop_views) + list(_spark_interop_views)
    ))

    for obj_name in all_objects:
        if not session:
            continue
        ok = False
        with suppress(Exception):
            session.sql(f"DROP TABLE IF EXISTS {obj_name}").collect()
            ok = True
        if not ok:
            with suppress(Exception):
                session.sql(f"DROP VIEW IF EXISTS {obj_name}").collect()
                ok = True
        if ok:
            dropped += 1

    _interop_views.clear()
    _spark_interop_views.clear()

    return dropped


# =============================================================================
# Diagnostics
# =============================================================================

def check_environment() -> Dict[str, Any]:
    """Run comprehensive environment diagnostics."""
    diag = {}

    # JVM
    diag["jvm"] = _check_jvm()

    # Scala interpreter
    diag["interpreter"] = _check_interpreter()

    # Snowpark
    diag["snowpark"] = _check_snowpark()

    # Snowflake env vars
    diag["snowflake_env"] = _check_snowflake_env()

    # Disk space
    diag["disk_space"] = _check_disk_space()

    return diag


def _check_jvm() -> Dict[str, Any]:
    result = {"ok": False, "details": {}, "errors": []}
    try:
        import jpype
        result["details"]["jpype_installed"] = True
        result["details"]["jvm_started"] = jpype.isJVMStarted()
        if jpype.isJVMStarted():
            result["details"]["jvm_path"] = jpype.getDefaultJVMPath()
        result["ok"] = jpype.isJVMStarted()
    except ImportError:
        result["details"]["jpype_installed"] = False
        result["errors"].append("JPype1 not installed")
    return result


def _check_interpreter() -> Dict[str, Any]:
    result = {"ok": False, "details": {}, "errors": []}
    itype = _scala_state.get("interpreter_type")
    result["details"]["type"] = itype or "not initialized"
    result["details"]["magic_registered"] = _scala_state.get("magic_registered", False)

    if itype:
        try:
            success, output, _ = execute_scala("1 + 1")
            result["details"]["test_eval"] = success
            result["ok"] = success
        except Exception as e:
            result["errors"].append(f"Test eval failed: {e}")
    else:
        result["errors"].append("No interpreter initialized")
    return result


def _check_snowpark() -> Dict[str, Any]:
    result = {"ok": False, "details": {}, "errors": []}
    meta = _scala_state.get("metadata")
    if meta:
        cp_file = meta.get("snowpark_classpath_file", "")
        result["details"]["classpath_file"] = cp_file
        result["details"]["classpath_exists"] = os.path.isfile(cp_file)
        result["details"]["version"] = meta.get("snowpark_version", "unknown")
        if cp_file and os.path.isfile(cp_file):
            jars = _read_classpath_file(cp_file)
            result["details"]["jar_count"] = len(jars)
            result["ok"] = len(jars) > 0
        else:
            result["errors"].append(f"Classpath file not found: {cp_file}")
    else:
        result["errors"].append("No metadata loaded")
    return result


def _check_snowflake_env() -> Dict[str, Any]:
    result = {"ok": False, "details": {}, "errors": []}
    required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"]
    optional = [
        "SNOWFLAKE_URL", "SNOWFLAKE_ROLE", "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA", "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_TOKEN", "SNOWFLAKE_AUTH_TYPE",
    ]
    missing = []
    for var in required:
        val = os.environ.get(var)
        result["details"][var] = "SET" if val else "NOT SET"
        if not val:
            missing.append(var)
    for var in optional:
        result["details"][var] = "SET" if os.environ.get(var) else "NOT SET"
    if missing:
        result["errors"].append(f"Missing required vars: {', '.join(missing)}")
    result["ok"] = len(missing) == 0
    return result


def _check_disk_space() -> Dict[str, Any]:
    result = {"ok": False, "details": {}, "errors": []}
    try:
        total, used, free = shutil.disk_usage("/")
        result["details"]["free_gb"] = round(free / (1024**3), 2)
        result["details"]["used_pct"] = round(used / total * 100, 1)
        result["ok"] = result["details"]["free_gb"] >= 0.5
        if not result["ok"]:
            result["errors"].append(f"Low disk: {result['details']['free_gb']}GB free")
    except Exception as e:
        result["errors"].append(f"Disk check failed: {e}")
    return result


def print_diagnostics(diagnostics: Optional[Dict[str, Any]] = None) -> None:
    """Print formatted diagnostic results."""
    if diagnostics is None:
        diagnostics = check_environment()

    print("=" * 60)
    print("Scala/JVM Environment Diagnostics")
    print("=" * 60)

    for component, result in diagnostics.items():
        status = "[OK]" if result["ok"] else "[FAIL]"
        print(f"\n{status} {component.upper().replace('_', ' ')}")
        for key, value in result["details"].items():
            print(f"    {key}: {value}")
        if result.get("errors"):
            for error in result["errors"]:
                print(f"    ERROR: {error}")

    print("\n" + "=" * 60)
    all_ok = all(r["ok"] for r in diagnostics.values())
    if all_ok:
        print("All checks passed!")
    else:
        failed = [k for k, v in diagnostics.items() if not v["ok"]]
        print(f"Issues found in: {', '.join(failed)}")
    print("=" * 60)


# =============================================================================
# Convenience: One-line session bootstrap
# =============================================================================

def bootstrap_snowpark_scala(session) -> Tuple[bool, str]:
    """
    All-in-one: inject credentials from Python session and create a
    Snowpark Scala session in the interpreter.

    Args:
        session: Active Snowpark Python session

    Returns:
        (success, message)
    """
    # Guard: Julia installs native signal handlers that conflict with
    # JVM SSL/network operations, causing an unrecoverable SIGSEGV.
    if "juliacall" in sys.modules:
        return False, (
            "FATAL: Julia runtime is already initialised. Creating a "
            "Snowpark Scala session will crash the kernel (SIGSEGV) due "
            "to JVM/Julia signal handler conflict.\n"
            "Fix: call bootstrap_snowpark_scala(session) BEFORE "
            "setup_julia_environment(). Restart kernel and reorder cells."
        )

    creds = inject_session_credentials(session)
    if not creds.get("SNOWFLAKE_ACCOUNT"):
        return False, "Failed to extract account from session"

    if not creds.get("SNOWFLAKE_TOKEN"):
        return False, (
            "No auth token available. Inside a Workspace the "
            "SPCS token at /snowflake/session/token should be "
            "auto-detected. For external use, set "
            "os.environ['SNOWFLAKE_PAT'] before calling this."
        )

    code = create_snowpark_scala_session_code()
    success, output, errors = execute_scala(code)

    if success:
        return True, output
    else:
        return False, errors or output or "Session creation failed"


def enable_udf_registration(stage: str = "@~/scala_udfs") -> Tuple[bool, str]:
    """Wire up the REPL class output directory so Snowpark Scala can
    serialise and register UDFs / stored procedures from ``%%scala`` cells.

    Snowpark's ``session.udf.registerTemporary`` / ``registerPermanent``
    serialise the lambda closure and upload it to a stage.  When running
    inside a Scala REPL (IMain), the compiled class files live in a
    temporary directory that Snowpark doesn't know about by default.
    This function calls ``sfSession.addDependency()`` to register that
    directory so UDF serialization works.

    Args:
        stage: Stage location for permanent UDFs (default ``@~/scala_udfs``).
            Only used when the caller later calls ``registerPermanent``.
            Temporary UDFs don't need a stage.

    Returns:
        (success, message)

    Example::

        from scala_helpers import enable_udf_registration
        enable_udf_registration()

        # Then in a %%scala cell:
        # val doubleUdf = udf((x: Int) => x + x)
        # sfSession.udf.registerPermanent("double_it", (x: Int) => x + x, "@~/scala_udfs")
    """
    repl_class_dir = _scala_state.get("_repl_class_dir")
    if not repl_class_dir:
        return False, "No REPL class directory found. Call setup_scala_environment() first."

    if not os.path.isdir(repl_class_dir):
        os.makedirs(repl_class_dir, exist_ok=True)

    # Register the REPL class directory as a dependency
    code = f'sfSession.addDependency("{repl_class_dir}")'
    success, output, errors = execute_scala(code)
    if not success:
        return False, (
            f"Failed to add REPL dependency: {errors or output}\n"
            "Make sure sfSession exists (run bootstrap_snowpark_scala first)."
        )

    parts = [f"REPL class directory registered: {repl_class_dir}"]

    # Register Snowpark + JDBC driver JARs as dependencies.
    # The REPL classloader doesn't expose these to the UDF serializer,
    # so Snowpark can't auto-detect them.  Without the JDBC JAR the
    # server-side UDF deserialization fails with
    # NoClassDefFoundError: net/snowflake/client/util/SecretDetector.
    _UDF_JAR_KEYWORDS = ("snowpark", "snowflake-jdbc")
    snowpark_cp_file = _scala_state.get("metadata", {}).get("snowpark_classpath_file", "")
    added_jars = []
    if snowpark_cp_file and os.path.isfile(snowpark_cp_file):
        with open(snowpark_cp_file) as f:
            for jar_path in f.read().strip().split(":"):
                jar_path = jar_path.strip()
                if not jar_path or not os.path.isfile(jar_path):
                    continue
                basename = os.path.basename(jar_path).lower()
                if any(kw in basename for kw in _UDF_JAR_KEYWORDS):
                    add_code = f'sfSession.addDependency("{jar_path}")'
                    ok, _, _ = execute_scala(add_code)
                    if ok:
                        added_jars.append(os.path.basename(jar_path))

    if added_jars:
        parts.append(f"Dependencies added: {', '.join(added_jars)}")

    parts.append(
        "UDF registration enabled. You can now use:\n"
        "  val myUdf = udf((x: Int) => x + x)\n"
        f'  sfSession.udf.registerPermanent("name", (x: Int) => x + x, "{stage}")'
    )

    return True, "\n".join(parts)


def enable_udf_registration_java(
    stage: str = "@~/java_udfs",
) -> Tuple[bool, str]:
    """Wire up dependencies so Snowpark Java can register UDFs / stored
    procedures from ``%%java`` cells.

    Snowpark Java's ``javaSession.udf().registerTemporary`` /
    ``registerPermanent`` serialise the lambda and upload it to a stage.
    In the JShell REPL context, Snowpark can't auto-detect its own JARs,
    so we explicitly add the Snowpark and JDBC driver JARs via
    ``javaSession.addDependency()``.

    Args:
        stage: Stage location for permanent UDFs
            (default ``@~/java_udfs``).

    Returns:
        (success, message)

    Example::

        from scala_helpers import enable_udf_registration_java
        enable_udf_registration_java()

        # Then in a %%java cell:
        # import com.snowflake.snowpark_java.types.*;
        # var doubleUdf = javaSession.udf().registerTemporary(
        #     (Integer x) -> x + x,
        #     DataTypes.IntegerType, DataTypes.IntegerType);
    """
    jshell = _java_state.get("jshell")
    if jshell is None:
        return False, (
            "No JShell interpreter initialized. "
            "Call setup_scala_environment() first."
        )

    parts = []

    # Register Snowpark + JDBC driver JARs as dependencies.
    _UDF_JAR_KEYWORDS = ("snowpark", "snowflake-jdbc")
    metadata = _scala_state.get("metadata", {})
    snowpark_cp_file = metadata.get(
        "snowpark_classpath_file", ""
    )
    added_jars = []
    if snowpark_cp_file and os.path.isfile(snowpark_cp_file):
        with open(snowpark_cp_file) as f:
            for jar_path in f.read().strip().split(":"):
                jar_path = jar_path.strip()
                if (
                    not jar_path
                    or not os.path.isfile(jar_path)
                ):
                    continue
                basename = os.path.basename(jar_path).lower()
                if any(
                    kw in basename
                    for kw in _UDF_JAR_KEYWORDS
                ):
                    add_code = (
                        f'javaSession.addDependency('
                        f'"{jar_path}");'
                    )
                    ok, _, _ = execute_java(add_code)
                    if ok:
                        added_jars.append(
                            os.path.basename(jar_path)
                        )

    if added_jars:
        parts.append(
            f"Dependencies added: {', '.join(added_jars)}"
        )

    parts.append(
        "Java UDF registration enabled. You can now use:\n"
        "  import com.snowflake.snowpark_java.types.*;\n"
        "  var doubleUdf = javaSession.udf()"
        ".registerTemporary(\n"
        "      (Integer x) -> x + x,\n"
        "      DataTypes.IntegerType, "
        "DataTypes.IntegerType);"
    )

    return True, "\n".join(parts)
