"""Micromamba download, install, and lifecycle management."""

from __future__ import annotations

import logging
import os
import platform
import stat
import tempfile

from .download import retry
from .subprocess_utils import run_cmd

logger = logging.getLogger("sfnb_multilang.shared.micromamba")

MIN_ARCHIVE_BYTES = 1_000_000  # bzip2 archive should be >1 MB
MIN_BINARY_BYTES = 3_000_000   # standalone binary should be >3 MB


def _get_platform_slug() -> str:
    """Return the micromamba platform slug (e.g. 'linux-64', 'osx-arm64')."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        arch = "64" if machine in ("x86_64", "amd64") else "aarch64"
        return f"linux-{arch}"
    elif system == "darwin":
        arch = "arm64" if machine == "arm64" else "64"
        return f"osx-{arch}"
    else:
        return "linux-64"


def _make_executable(path: str) -> None:
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _download_via_archive(target_dir: str, binary_path: str) -> str:
    """Download bzip2 archive from micro.mamba.pm, extract to target_dir."""
    slug = _get_platform_slug()
    url = f"https://micro.mamba.pm/api/micromamba/{slug}/latest"
    logger.info("Downloading micromamba archive from %s ...", url)

    with tempfile.NamedTemporaryFile(suffix=".tar.bz2", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        run_cmd(
            ["curl", "-fSL", "--retry", "3", "--retry-delay", "2",
             "-o", tmp_path, url],
            description="Download micromamba archive",
        )
        size = os.path.getsize(tmp_path)
        if size < MIN_ARCHIVE_BYTES:
            raise RuntimeError(
                f"Downloaded archive too small ({size} bytes); "
                f"expected >{MIN_ARCHIVE_BYTES}"
            )
        run_cmd(
            ["tar", "-xjf", tmp_path, "-C", target_dir, "bin/micromamba"],
            description="Extract micromamba",
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    _make_executable(binary_path)
    return binary_path


def _download_standalone(binary_path: str) -> str:
    """Download standalone binary from GitHub Releases (no extraction)."""
    slug = _get_platform_slug()
    url = (
        "https://github.com/mamba-org/micromamba-releases"
        f"/releases/latest/download/micromamba-{slug}"
    )
    logger.info("Downloading standalone micromamba from %s ...", url)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:
        run_cmd(
            ["curl", "-fSL", "--retry", "3", "--retry-delay", "2",
             "-o", tmp_path, url],
            description="Download micromamba binary",
        )
        size = os.path.getsize(tmp_path)
        if size < MIN_BINARY_BYTES:
            raise RuntimeError(
                f"Downloaded binary too small ({size} bytes); "
                f"expected >{MIN_BINARY_BYTES}"
            )
        os.replace(tmp_path, binary_path)
        tmp_path = None  # don't unlink in finally
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    _make_executable(binary_path)
    return binary_path


def _download_urllib(binary_path: str) -> str:
    """Last-resort download using Python urllib (no curl dependency)."""
    import urllib.request

    slug = _get_platform_slug()
    url = (
        "https://github.com/mamba-org/micromamba-releases"
        f"/releases/latest/download/micromamba-{slug}"
    )
    logger.info("Downloading micromamba via urllib from %s ...", url)
    urllib.request.urlretrieve(url, binary_path)

    size = os.path.getsize(binary_path)
    if size < MIN_BINARY_BYTES:
        os.unlink(binary_path)
        raise RuntimeError(
            f"Downloaded binary too small ({size} bytes); "
            f"expected >{MIN_BINARY_BYTES}"
        )
    _make_executable(binary_path)
    return binary_path


def _download_custom_url(binary_path: str, url: str, ssl_cert_path: str = "") -> str:
    """Download micromamba from a custom URL (Artifactory / Nexus / etc.).

    Supports both raw binaries and bzip2 archives (auto-detected by URL
    suffix or content).  For corporate environments with TLS inspection,
    pass ssl_cert_path to a custom CA bundle.
    """
    import urllib.request
    import ssl

    logger.info("Downloading micromamba from custom URL: %s ...", url)

    if ssl_cert_path and os.path.isfile(ssl_cert_path):
        ctx = ssl.create_default_context(cafile=ssl_cert_path)
    else:
        ctx = None

    from urllib.parse import urlparse, urlunparse
    import base64

    parsed = urlparse(url)
    headers = {"User-Agent": "sfnb-multilang"}
    if parsed.username:
        from urllib.parse import unquote
        raw_user = unquote(parsed.username)
        raw_pass = unquote(parsed.password or "")
        token = base64.b64encode(f"{raw_user}:{raw_pass}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
        clean_netloc = parsed.hostname
        if parsed.port:
            clean_netloc += f":{parsed.port}"
        url = urlunparse(parsed._replace(netloc=clean_netloc))

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:
        req = urllib.request.Request(url, headers=headers)
        kwargs = {"timeout": 120}
        if ctx:
            kwargs["context"] = ctx
        with urllib.request.urlopen(req, **kwargs) as resp:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)

        size = os.path.getsize(tmp_path)

        if url.endswith((".tar.bz2", ".bz2")):
            target_dir = os.path.dirname(os.path.dirname(binary_path))
            if size < MIN_ARCHIVE_BYTES:
                raise RuntimeError(
                    f"Downloaded archive too small ({size} bytes); "
                    f"expected >{MIN_ARCHIVE_BYTES}"
                )
            run_cmd(
                ["tar", "-xjf", tmp_path, "-C", target_dir, "bin/micromamba"],
                description="Extract micromamba from custom archive",
            )
        else:
            if size < MIN_BINARY_BYTES:
                raise RuntimeError(
                    f"Downloaded binary too small ({size} bytes); "
                    f"expected >{MIN_BINARY_BYTES}"
                )
            os.replace(tmp_path, binary_path)
            tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    _make_executable(binary_path)
    return binary_path


@retry(max_attempts=3, delay=5)
def download_micromamba(
    target_dir: str,
    custom_url: str = "",
    ssl_cert_path: str = "",
) -> str:
    """Download the micromamba binary to target_dir/bin/micromamba.

    If custom_url is provided (e.g. an Artifactory generic repo), it is
    tried first.  Otherwise falls back to the standard strategies:
      1. bzip2 archive from micro.mamba.pm
      2. Standalone binary from GitHub Releases via curl
      3. Standalone binary from GitHub Releases via Python urllib
    """
    bin_dir = os.path.join(target_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    binary_path = os.path.join(bin_dir, "micromamba")

    strategies: list[tuple[str, object]] = []
    if custom_url:
        strategies.append((
            "custom-url",
            lambda: _download_custom_url(binary_path, custom_url, ssl_cert_path),
        ))
    strategies.extend([
        ("archive", lambda: _download_via_archive(target_dir, binary_path)),
        ("github-curl", lambda: _download_standalone(binary_path)),
        ("github-urllib", lambda: _download_urllib(binary_path)),
    ])

    last_exc: Exception | None = None
    for name, fn in strategies:
        try:
            result = fn()
            logger.info("micromamba installed at %s (via %s)", result, name)
            return result
        except Exception as exc:
            logger.warning("micromamba download via %s failed: %s", name, exc)
            last_exc = exc

    raise RuntimeError(
        f"All micromamba download strategies failed: {last_exc}"
    ) from last_exc


def ensure_micromamba(
    root: str,
    force: bool = False,
    custom_url: str = "",
    ssl_cert_path: str = "",
) -> str:
    """Return path to micromamba binary, downloading if needed.

    Args:
        root: Directory where micromamba lives (contains bin/micromamba).
        force: Re-download even if already present.
        custom_url: Optional URL to download micromamba from (e.g.
            an Artifactory/Nexus generic repo).
        ssl_cert_path: Path to a custom CA cert bundle for TLS inspection.

    Returns:
        Absolute path to the micromamba binary.
    """
    root = os.path.expanduser(root)
    binary = os.path.join(root, "bin", "micromamba")

    if os.path.isfile(binary) and os.access(binary, os.X_OK) and not force:
        logger.info("micromamba already installed (skipping)")
        return binary

    return download_micromamba(root, custom_url=custom_url, ssl_cert_path=ssl_cert_path)
