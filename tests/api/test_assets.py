"""Tests for admin static-asset cache-bust version resolver (src.api.admin.assets)."""

import subprocess
from unittest.mock import patch

import pytest
from fastapi.templating import Jinja2Templates

from src.api.admin import assets
from tests.api.jinja_templates_walker import walk_admin_jinja_templates


@pytest.fixture
def admin_templates_injected():
    """Run the production injector once, decoupled from src.api.main.

    Calling the injector directly avoids the side-effecting import chain that
    `src.api.main` triggers (configure_logging(), DB module load, every admin
    submodule), keeping the test's failure modes tied to what we're actually
    asserting. Scoped (not autouse) so the unit tests above don't pay the
    walk cost or inherit the failure mode of an unrelated import error in
    the admin package.
    """
    assets.inject_asset_version_into_admin_templates()


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


def test_every_admin_jinja_env_has_asset_version_global(admin_templates_injected):
    """Every Jinja2Templates instance in src.api.admin must have asset_version injected.

    Guards against the failure mode the production injector
    (`assets.inject_asset_version_into_admin_templates` — `pkgutil.iter_modules`,
    one level only) would silently introduce: a new `src.api.admin.<subpkg>.<mod>`
    that defines templates would render `?v=` (empty) instead of the cache-bust
    value. If this test fails, widen the injector (e.g. switch to `walk_packages`)
    or move the templates to a top-level admin module.
    """
    instances = walk_admin_jinja_templates()
    assert instances, "expected at least one Jinja2Templates in src.api.admin"
    missing = [name for name, t in instances if "asset_version" not in t.env.globals]
    assert not missing, (
        f"asset_version global missing on {len(missing)} Jinja2Templates instance(s): "
        f"{missing}. The injector in src.api.admin.assets probably skipped them — "
        "check whether they live in a subpackage."
    )


def test_admin_jinja_envs_render_asset_version_to_real_string(admin_templates_injected):
    """End-to-end: a real admin Jinja env must render `{{ asset_version }}` to ASSET_VERSION."""
    instances = walk_admin_jinja_templates()
    assert instances
    _, templates = instances[0]
    rendered = templates.env.from_string("v={{ asset_version }}").render()
    assert rendered == f"v={assets.ASSET_VERSION}"
