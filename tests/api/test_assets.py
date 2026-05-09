"""Tests for admin static-asset cache-bust version resolver (src.api.admin.assets)."""

import subprocess
from unittest.mock import patch

from fastapi.templating import Jinja2Templates

from src.api.admin import assets


def test_compute_asset_version_uses_git_short_hash_when_available():
    """When `git rev-parse --short HEAD` succeeds, its trimmed stdout is returned."""
    with patch.object(assets.subprocess, "check_output", return_value=b"abc1234\n"):
        result = assets.compute_asset_version()
    assert result == "abc1234"


def test_compute_asset_version_falls_back_when_git_missing():
    """If `git` binary is absent, fall back to a unix-timestamp string."""
    with patch.object(assets.subprocess, "check_output", side_effect=FileNotFoundError):
        result = assets.compute_asset_version()
    assert result.isdigit()
    assert int(result) > 0


def test_compute_asset_version_falls_back_when_not_a_git_checkout():
    """If `git rev-parse` fails (non-zero exit), fall back to a timestamp string."""
    err = subprocess.CalledProcessError(returncode=128, cmd=["git", "rev-parse"])
    with patch.object(assets.subprocess, "check_output", side_effect=err):
        result = assets.compute_asset_version()
    assert result.isdigit()
    assert int(result) > 0


def test_compute_asset_version_falls_back_on_oserror():
    """A generic OSError from subprocess must not propagate; fall back instead."""
    with patch.object(assets.subprocess, "check_output", side_effect=OSError("bad pipe")):
        result = assets.compute_asset_version()
    assert result.isdigit()


def test_register_asset_version_global_sets_jinja_global(tmp_path):
    """register_asset_version_global injects `asset_version` into the Jinja env."""
    (tmp_path / "t.html").write_text("ver={{ asset_version }}")
    templates = Jinja2Templates(directory=str(tmp_path))
    assets.register_asset_version_global(templates, version="deadbeef")
    assert templates.env.globals["asset_version"] == "deadbeef"


def test_module_level_asset_version_is_a_string():
    """The cached module-level ASSET_VERSION must be a non-empty string."""
    assert isinstance(assets.ASSET_VERSION, str)
    assert assets.ASSET_VERSION
