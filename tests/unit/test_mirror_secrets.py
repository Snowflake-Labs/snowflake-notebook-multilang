"""Unit tests for Workspace mirror authentication (PASSWORD secrets).

These tests mock the Snowpark Secrets API and filesystem reads so CI
proves the bootstrap credential path without Snowflake or Artifactory.
"""

from __future__ import annotations

import io
import sys
import types
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# inject_auth_into_url / mask_url_credentials
# ---------------------------------------------------------------------------

class TestInjectAuthIntoUrl:

    def test_injects_basic_auth(self):
        from sfnb_multilang.installer import inject_auth_into_url

        url = "https://artifactory.example.com/api/pypi/simple"
        out = inject_auth_into_url(url, "svc", "mytoken")
        assert out == "https://svc:mytoken@artifactory.example.com/api/pypi/simple"

    def test_preserves_port(self):
        from sfnb_multilang.installer import inject_auth_into_url

        url = "https://artifactory.example.com:8443/simple"
        out = inject_auth_into_url(url, "u", "p")
        assert ":8443" in out
        assert "u:p@artifactory.example.com:8443" in out

    def test_url_already_has_username_unchanged(self):
        from sfnb_multilang.installer import inject_auth_into_url

        url = "https://existing:old@host/simple"
        assert inject_auth_into_url(url, "u", "p") == url

    def test_empty_username_no_injection(self):
        from sfnb_multilang.installer import inject_auth_into_url

        url = "https://host/simple"
        assert inject_auth_into_url(url, "", "only-password") == url

    def test_special_chars_quoted(self):
        from sfnb_multilang.installer import inject_auth_into_url

        url = "https://host/simple"
        out = inject_auth_into_url(url, "user@corp", "p@ss:w/rd")
        assert "user%40corp" in out
        assert "p%40ss%3Aw%2Frd" in out


class TestMaskUrlCredentials:

    def test_masks_password(self):
        from sfnb_multilang.installer import mask_url_credentials

        u = mask_url_credentials("https://svc:secret@host/path")
        assert "secret" not in u
        assert "svc:****@host" in u


# ---------------------------------------------------------------------------
# read_mirror_credentials (Snowpark API + mount fallback)
# ---------------------------------------------------------------------------

def _install_fake_snowflake_snowpark(
    monkeypatch: pytest.MonkeyPatch,
    *,
    username: str = "api_user",
    password: str = "api_pass",
    raise_from_api: bool = False,
):
    """Register minimal fake ``snowflake.snowpark.secrets`` in sys.modules."""

    def get_username_password(secret_path: str):
        if raise_from_api:
            raise RuntimeError("simulated no active session")
        assert "/" in secret_path  # normalized to slash form
        return types.SimpleNamespace(username=username, password=password)

    secrets_mod = types.ModuleType("snowflake.snowpark.secrets")
    secrets_mod.get_username_password = get_username_password
    snowpark_mod = types.ModuleType("snowflake.snowpark")
    snowpark_mod.secrets = secrets_mod
    snowflake_mod = types.ModuleType("snowflake")
    snowflake_mod.snowpark = snowpark_mod

    monkeypatch.setitem(sys.modules, "snowflake", snowflake_mod)
    monkeypatch.setitem(sys.modules, "snowflake.snowpark", snowpark_mod)
    monkeypatch.setitem(sys.modules, "snowflake.snowpark.secrets", secrets_mod)


class TestReadMirrorCredentials:

    def test_empty_auth_secret(self):
        from sfnb_multilang.installer import read_mirror_credentials

        assert read_mirror_credentials("") == ("", "")

    def test_snowpark_api_dot_notation_normalized(self, monkeypatch):
        _install_fake_snowflake_snowpark(monkeypatch, username="u1", password="p1")
        from sfnb_multilang.installer import read_mirror_credentials

        u, p = read_mirror_credentials("MYDB.MYSCHEMA.MYSECRET")
        assert u == "u1"
        assert p == "p1"

    def test_snowpark_api_slash_notation_unchanged(self, monkeypatch):
        _install_fake_snowflake_snowpark(monkeypatch)
        from sfnb_multilang.installer import read_mirror_credentials

        u, p = read_mirror_credentials("mydb/myschema/mysecret")
        assert u == "api_user"
        assert p == "api_pass"

    def test_mount_fallback_when_api_fails(self, monkeypatch):
        _install_fake_snowflake_snowpark(monkeypatch, raise_from_api=True)
        from sfnb_multilang.installer import read_mirror_credentials

        expected_user = "/secrets/mydb/myschema/sec/username"
        expected_pass = "/secrets/mydb/myschema/sec/password"

        def fake_open(path, *args, **kwargs):
            s = str(path)
            if s == expected_user:
                return io.StringIO("mount_u\n")
            if s == expected_pass:
                return io.StringIO("mount_p\n")
            raise FileNotFoundError(s)

        with patch("builtins.open", fake_open):
            u, p = read_mirror_credentials("mydb.myschema.sec")
        assert u == "mount_u"
        assert p == "mount_p"

    def test_returns_empty_when_unreadable(self, monkeypatch):
        """No fake snowflake + no files -> ('', '') after warning path."""
        # Ensure snowflake import fails inside read_mirror_credentials
        monkeypatch.delitem(sys.modules, "snowflake", raising=False)
        monkeypatch.delitem(sys.modules, "snowflake.snowpark", raising=False)
        monkeypatch.delitem(sys.modules, "snowflake.snowpark.secrets", raising=False)

        with patch("builtins.open", side_effect=FileNotFoundError("no")):
            from sfnb_multilang.installer import read_mirror_credentials

            u, p = read_mirror_credentials("db.schema.missing")
        assert u == ""
        assert p == ""


class TestMirrorAuthEndToEnd:

    def test_installer_inject_after_read(self, monkeypatch):
        """Same flow as installer: creds from API -> authenticated mirror URL."""
        _install_fake_snowflake_snowpark(
            monkeypatch, username="deploy", password="key123",
        )
        from sfnb_multilang.installer import inject_auth_into_url, read_mirror_credentials

        base = "https://artifactory.example.com/api/pypi/remote/simple"
        user, pw = read_mirror_credentials("snowpublic.notebooks.creds")
        out = inject_auth_into_url(base, user, pw)
        assert out.startswith("https://deploy:key123@artifactory.example.com/")
