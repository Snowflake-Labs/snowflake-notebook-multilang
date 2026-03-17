"""Scala/Java language plugin -- ported from setup_scala_environment.sh."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from ..config import ToolkitConfig
from ..exceptions import PackageInstallError, PluginError
from ..shared.download import retry
from ..shared.subprocess_utils import run_cmd
from .base import LanguagePlugin, PluginResult

logger = logging.getLogger("sfnb_multilang.languages.scala")

DEFAULT_JAR_DIR = os.path.expanduser("~/scala_jars")


class ScalaPlugin(LanguagePlugin):

    @property
    def name(self) -> str:
        return "scala"

    @property
    def display_name(self) -> str:
        return "Scala/Java"

    # -----------------------------------------------------------------
    # Package declarations
    # -----------------------------------------------------------------

    def get_conda_packages(self, config: ToolkitConfig) -> list[str]:
        java_version = config.scala.java_version
        return [f"openjdk={java_version}"]

    def get_pip_packages(self, config: ToolkitConfig) -> list[str]:
        packages = ["JPype1"]
        sc = config.scala.spark_connect
        if sc.get("enabled"):
            packages.extend([
                "snowpark-connect[jdk]",
                f"pyspark=={sc.get('pyspark_version', '3.5.6')}",
                "opentelemetry-exporter-otlp",
            ])
        return packages

    def get_network_hosts(self, config: ToolkitConfig) -> list[dict]:
        return [
            {"host": "repo1.maven.org", "port": 443,
             "purpose": "Maven Central (Snowpark, Scala, Ammonite JARs)",
             "required": True},
            {"host": "github.com", "port": 443,
             "purpose": "coursier JAR launcher download",
             "required": True},
            {"host": "objects.githubusercontent.com", "port": 443,
             "purpose": "GitHub raw content", "required": True},
            {"host": "pypi.org", "port": 443,
             "purpose": "pip packages (JPype1)", "required": True},
            {"host": "files.pythonhosted.org", "port": 443,
             "purpose": "pip package files", "required": True},
        ]

    # -----------------------------------------------------------------
    # Post-install
    # -----------------------------------------------------------------

    def post_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        sc = config.scala
        jar_dir = DEFAULT_JAR_DIR
        os.makedirs(jar_dir, exist_ok=True)

        java_home = env_prefix
        os.environ["JAVA_HOME"] = java_home
        os.environ["PATH"] = os.path.join(env_prefix, "bin") + os.pathsep + os.environ.get("PATH", "")

        # Verify Java
        logger.info("  JAVA_HOME: %s", java_home)
        try:
            result = run_cmd(["java", "-version"], description="Verify Java", check=False)
            java_ver_line = (result.stderr or result.stdout or "").split("\n")[0]
            logger.info("  Java: %s", java_ver_line.strip())
        except Exception:
            raise PluginError("scala", "post_install", "Java not found after installation")

        # coursier
        cs_jar = self._ensure_coursier(jar_dir, config.force_reinstall)

        # Snowpark JARs
        snowpark_cp = self._resolve_snowpark(cs_jar, jar_dir, sc, config.force_reinstall)

        # Scala compiler + Ammonite
        scala_cp, scala_full_version = self._resolve_scala(
            cs_jar, jar_dir, sc, config.force_reinstall)
        ammonite_cp = self._resolve_ammonite(
            cs_jar, jar_dir, sc, scala_full_version, config.force_reinstall)

        # Extra deps
        extra_cp = self._resolve_extras(cs_jar, jar_dir, sc, config.force_reinstall)

        # Spark Connect (optional)
        spark_cp = ""
        if sc.spark_connect.get("enabled"):
            spark_cp = self._setup_spark_connect(
                cs_jar, jar_dir, sc, config.force_reinstall)

        # Write metadata
        self._write_metadata(
            jar_dir, env_prefix, sc, scala_full_version,
            snowpark_cp, scala_cp, ammonite_cp, extra_cp, spark_cp,
        )

        return PluginResult(
            success=True, language="scala",
            version=scala_full_version or sc.scala_version,
            env_prefix=env_prefix,
        )

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate_install(self, env_prefix: str, config: ToolkitConfig) -> PluginResult:
        errors: list[str] = []

        java_bin = os.path.join(env_prefix, "bin", "java")
        if not os.path.isfile(java_bin):
            errors.append(f"Java binary not found at {java_bin}")

        metadata_file = os.path.join(DEFAULT_JAR_DIR, "scala_env_metadata.json")
        if not os.path.isfile(metadata_file):
            errors.append(f"Metadata file not found at {metadata_file}")
        else:
            try:
                with open(metadata_file) as f:
                    meta = json.load(f)
                logger.info("  Scala validation: %s, Snowpark %s",
                            meta.get("scala_full_version", "?"),
                            meta.get("snowpark_version", "?"))
            except Exception as exc:
                errors.append(f"Failed to read metadata: {exc}")

        return PluginResult(
            success=len(errors) == 0, language="scala",
            env_prefix=env_prefix, errors=errors,
        )

    # -----------------------------------------------------------------
    # Internal: coursier
    # -----------------------------------------------------------------

    @retry(max_attempts=3, delay=5)
    def _ensure_coursier(self, jar_dir: str, force: bool) -> str:
        cs_jar = os.path.join(jar_dir, "coursier.jar")
        if not force and os.path.isfile(cs_jar):
            logger.info("  coursier JAR already downloaded (skipping)")
            return cs_jar

        logger.info("  Downloading coursier JAR launcher...")
        run_cmd(
            ["curl", "-fL", "-o", cs_jar,
             "https://github.com/coursier/coursier/releases/latest/download/coursier"],
            description="Download coursier",
        )
        logger.info("    coursier saved to %s", cs_jar)
        return cs_jar

    def _cs_fetch(self, cs_jar: str, *args: str) -> str:
        """Run coursier fetch and return classpath string."""
        env = dict(os.environ)
        env.pop("JAVA_TOOL_OPTIONS", None)
        result = run_cmd(
            ["java", "-jar", cs_jar, "fetch", *args, "--classpath"],
            description="coursier fetch",
            env=env,
        )
        return result.stdout.strip()

    # -----------------------------------------------------------------
    # Internal: JAR resolution
    # -----------------------------------------------------------------

    @retry(max_attempts=3, delay=5)
    def _resolve_snowpark(self, cs_jar: str, jar_dir: str,
                          sc: Any, force: bool) -> str:
        cp_file = os.path.join(jar_dir, "snowpark_classpath.txt")
        if not force and os.path.isfile(cp_file):
            logger.info("  Snowpark classpath already resolved (skipping)")
            with open(cp_file) as f:
                return f.read().strip()

        artifact = f"com.snowflake:snowpark_{sc.scala_version}:{sc.snowpark_version}"
        logger.info("  Resolving %s ...", artifact)
        classpath = self._cs_fetch(cs_jar, artifact)
        with open(cp_file, "w") as f:
            f.write(classpath)

        jar_count = len(classpath.split(":"))
        logger.info("    Snowpark resolved: %d JARs", jar_count)
        return classpath

    @retry(max_attempts=3, delay=5)
    def _resolve_scala(self, cs_jar: str, jar_dir: str,
                       sc: Any, force: bool) -> tuple[str, str]:
        cp_file = os.path.join(jar_dir, "scala_classpath.txt")
        if not force and os.path.isfile(cp_file):
            logger.info("  Scala classpath already resolved (skipping)")
            with open(cp_file) as f:
                cp = f.read().strip()
        else:
            logger.info("  Resolving Scala %s compiler JARs...", sc.scala_version)
            cp = self._cs_fetch(
                cs_jar,
                f"org.scala-lang:scala-compiler:{sc.scala_version}+",
                f"org.scala-lang:scala-reflect:{sc.scala_version}+",
            )
            with open(cp_file, "w") as f:
                f.write(cp)

        # Extract full version from scala-library JAR path
        full_version = ""
        for entry in cp.split(":"):
            match = re.search(r"scala-library[/-](\d+\.\d+\.\d+)", entry)
            if match:
                full_version = match.group(1)
                break
        logger.info("    Scala full version: %s", full_version or "unknown")
        return cp, full_version

    @retry(max_attempts=3, delay=5)
    def _resolve_ammonite(self, cs_jar: str, jar_dir: str,
                          sc: Any, scala_full_version: str,
                          force: bool) -> str:
        cp_file = os.path.join(jar_dir, "ammonite_classpath.txt")
        if not force and os.path.isfile(cp_file):
            logger.info("  Ammonite classpath already resolved (skipping)")
            with open(cp_file) as f:
                return f.read().strip()

        scala_ver = scala_full_version or sc.scala_version
        artifact = f"com.lihaoyi:ammonite_{scala_ver}:{sc.ammonite_version}"
        logger.info("  Resolving %s ...", artifact)
        try:
            cp = self._cs_fetch(cs_jar, artifact)
            with open(cp_file, "w") as f:
                f.write(cp)
            jar_count = len(cp.split(":"))
            logger.info("    Ammonite classpath: %d JARs", jar_count)
            return cp
        except Exception:
            logger.warning("  Ammonite resolution failed. Will fall back to IMain.")
            with open(cp_file, "w") as f:
                f.write("")
            return ""

    def _resolve_extras(self, cs_jar: str, jar_dir: str,
                        sc: Any, force: bool) -> str:
        cp_file = os.path.join(jar_dir, "extra_classpath.txt")
        if not sc.extra_dependencies:
            with open(cp_file, "w") as f:
                f.write("")
            return ""

        logger.info("  Resolving extra dependencies...")
        parts: list[str] = []
        for dep in sc.extra_dependencies:
            logger.info("    Resolving %s ...", dep)
            try:
                cp = self._cs_fetch(cs_jar, dep)
                parts.append(cp)
            except Exception:
                logger.warning("    Failed to resolve: %s", dep)

        combined = ":".join(p for p in parts if p)
        with open(cp_file, "w") as f:
            f.write(combined)
        return combined

    def _setup_spark_connect(self, cs_jar: str, jar_dir: str,
                             sc: Any, force: bool) -> str:
        logger.info("  Setting up Spark Connect for Scala...")
        cp_file = os.path.join(jar_dir, "spark_connect_classpath.txt")

        if not force and os.path.isfile(cp_file) and os.path.getsize(cp_file) > 0:
            logger.info("    Spark Connect client JARs already resolved (skipping)")
            with open(cp_file) as f:
                return f.read().strip()

        pyspark_ver = sc.spark_connect.get("pyspark_version", "3.5.6")
        artifact = f"org.apache.spark:spark-connect-client-jvm_{sc.scala_version}:{pyspark_ver}"
        logger.info("    Resolving %s ...", artifact)
        try:
            cp = self._cs_fetch(cs_jar, artifact)
            with open(cp_file, "w") as f:
                f.write(cp)
            logger.info("    Spark Connect setup complete")
            return cp
        except Exception:
            logger.warning("    Spark Connect JAR resolution failed")
            return ""

    # -----------------------------------------------------------------
    # Internal: metadata
    # -----------------------------------------------------------------

    def _write_metadata(
        self, jar_dir: str, env_prefix: str, sc: Any,
        scala_full_version: str,
        snowpark_cp: str, scala_cp: str, ammonite_cp: str,
        extra_cp: str, spark_cp: str,
    ) -> None:
        logger.info("  Writing Scala metadata...")
        metadata = {
            "env_prefix": env_prefix,
            "java_home": env_prefix,
            "java_version": sc.java_version,
            "scala_version": sc.scala_version,
            "scala_full_version": scala_full_version,
            "snowpark_version": sc.snowpark_version,
            "snowpark_classpath_file": os.path.join(jar_dir, "snowpark_classpath.txt"),
            "ammonite_version": sc.ammonite_version,
            "jar_dir": jar_dir,
            "scala_classpath_file": os.path.join(jar_dir, "scala_classpath.txt"),
            "ammonite_classpath_file": os.path.join(jar_dir, "ammonite_classpath.txt"),
            "extra_classpath_file": os.path.join(jar_dir, "extra_classpath.txt"),
            "jvm_heap": sc.jvm_heap,
            "jvm_options": sc.jvm_options,
            "spark_connect_enabled": sc.spark_connect.get("enabled", False),
            "spark_connect_classpath_file": (
                os.path.join(jar_dir, "spark_connect_classpath.txt")
                if spark_cp else None
            ),
            "spark_connect_server_port": sc.spark_connect.get("server_port", 15002),
        }
        meta_path = os.path.join(jar_dir, "scala_env_metadata.json")
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.info("    Metadata written to %s", meta_path)
