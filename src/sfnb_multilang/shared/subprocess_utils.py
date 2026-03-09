"""Wrapper around subprocess.run with logging, timing, and error context."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Optional

logger = logging.getLogger("sfnb_multilang.shared.subprocess")


def run_cmd(
    cmd: list[str],
    description: str = "",
    check: bool = True,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    capture_output: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a command with logging, timing, and error context.

    Args:
        cmd: Command and arguments.
        description: Human-readable description for log messages.
        check: Raise CalledProcessError on non-zero exit.
        env: Environment variables (defaults to current environ).
        cwd: Working directory.
        capture_output: Capture stdout/stderr.
        timeout: Timeout in seconds.

    Returns:
        CompletedProcess result.

    Raises:
        subprocess.CalledProcessError: If check=True and command fails.
    """
    desc = description or " ".join(cmd[:3])
    logger.debug("Running: %s", " ".join(cmd))

    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            check=check,
            env=merged_env,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
        )
        elapsed = (time.monotonic() - start) * 1000
        logger.debug("  %s completed in %.0fms", desc, elapsed)
        return result
    except subprocess.CalledProcessError as exc:
        elapsed = (time.monotonic() - start) * 1000
        stdout_snippet = (exc.stdout or "")[:500]
        stderr_snippet = (exc.stderr or "")[:500]
        logger.error(
            "Command failed (%.0fms): %s\n  stdout: %s\n  stderr: %s",
            elapsed,
            " ".join(cmd),
            stdout_snippet or "<empty>",
            stderr_snippet or "<empty>",
        )
        raise
    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - start) * 1000
        logger.error("Command timed out after %.0fms: %s", elapsed, " ".join(cmd))
        raise
