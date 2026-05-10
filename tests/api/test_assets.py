"""Tests for admin static-asset cache-bust version resolver (src.api.admin.assets)."""

import importlib
import pkgutil
import subprocess
from unittest.mock import patch

import pytest
from fastapi.templating import Jinja2Templates

import src.api.admin as admin_pkg
from src.api.admin import assets


@pytest.fixture(autouse=True)
def _inject_for_test():
    """Run the production injector once per test, decoupled from src.api.main.

    Calling the injector directly avoids the side-effecting import chain that
    `src.api.main` triggers (configure_logging(), DB module load, every admin
    submodule), keeping the test's failure modes tied to what we're actually
    asserting.
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


def _walk_admin_jinja_templates() -> list[tuple[str, Jinja2Templates]]:
    """Yield every (module_name, Jinja2Templates) instance reachable in src.api.admin.

    Uses ``walk_packages`` to recurse into subpackages so future
    ``src.api.admin.<subpkg>.<mod>`` layouts are covered too — the production
    injector in ``assets.inject_asset_version_into_admin_templates`` only
    walks the top level, so this test surfaces injection gaps before they
    ship.

    Iterates ``module.__dict__.items()`` directly (faster than ``dir()`` +
    ``getattr()``, and avoids triggering attribute descriptors). Lets
    ``ImportError`` propagate: a module that fails to import is the worst
    case — its templates would receive zero injection — and silently
    skipping it would mask the very gap this test exists to detect.
    """
    found: list[tuple[str, Jinja2Templates]] = []
    for mod_info in pkgutil.walk_packages(admin_pkg.__path__, prefix=f"{admin_pkg.__name__}."):
        module = importlib.import_module(mod_info.name)
        for attr_name, attr in module.__dict__.items():
            if isinstance(attr, Jinja2Templates):
                found.append((f"{mod_info.name}.{attr_name}", attr))
    return found


def test_every_admin_jinja_env_has_asset_version_global():
    """Every Jinja2Templates instance in src.api.admin must have asset_version injected.

    Guards against the failure mode the production injector (`pkgutil.iter_modules`,
    one level only) would silently introduce: a new `src.api.admin.<subpkg>.<mod>`
    that defines templates would render `?v=` (empty) instead of the cache-bust
    value. If this test fails, the injector at `src/api/main.py:22-32` needs to
    recurse (e.g. switch to `walk_packages`) or the templates need to be moved
    to a top-level admin module.
    """
    instances = _walk_admin_jinja_templates()
    assert instances, "expected at least one Jinja2Templates in src.api.admin"
    missing = [name for name, t in instances if "asset_version" not in t.env.globals]
    assert not missing, (
        f"asset_version global missing on {len(missing)} Jinja2Templates instance(s): "
        f"{missing}. The injector in src/api/main.py probably skipped them — "
        "check whether they live in a subpackage."
    )


def test_admin_jinja_envs_render_asset_version_to_real_string():
    """End-to-end: a real admin Jinja env must render `{{ asset_version }}` to ASSET_VERSION."""
    instances = _walk_admin_jinja_templates()
    assert instances
    _, templates = instances[0]
    rendered = templates.env.from_string("v={{ asset_version }}").render()
    assert rendered == f"v={assets.ASSET_VERSION}"
