"""Tests for scripts/check_version_sync.sh.

Three sites carry the version: pyproject.toml, package.json and, since #618,
clients/python/pyproject.toml (the generated client ships at the app's version).
package-lock.json mirrors package.json in two fields, its root and ``packages[""]``
(#672: it sat at 0.31.0 for thirty-odd bumps because nothing read it).
"""

import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "check_version_sync.sh"
_SAME = object()  # client_version / lock default: whatever the sibling file says


def _run(
    tmp_path: Path,
    py_version: str | None,
    js_version: str | None,
    client_version: object = _SAME,
    lock_version: object = _SAME,
    lock_package_version: object = _SAME,
) -> subprocess.CompletedProcess:
    if py_version is not None:
        (tmp_path / "pyproject.toml").write_text(f'version = "{py_version}"\n')
    else:
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'x'\n")

    if js_version is not None:
        (tmp_path / "package.json").write_text(f'{{"name": "x", "version": "{js_version}"}}\n')
    else:
        (tmp_path / "package.json").write_text('{"name": "x"}\n')

    client = tmp_path / "clients" / "python" / "pyproject.toml"
    client.parent.mkdir(parents=True)
    client_version = py_version if client_version is _SAME else client_version
    if client_version is not None:
        client.write_text(f'[project]\n# stamped\nversion = "{client_version}"\n')
    else:
        client.write_text("[project]\nname = 'x'\n")

    lock_version = js_version if lock_version is _SAME else lock_version
    lock_package_version = lock_version if lock_package_version is _SAME else lock_package_version
    lock: dict = {"name": "x", "lockfileVersion": 3, "packages": {"": {"name": "x"}}}
    if lock_version is not None:
        lock["version"] = lock_version
    if lock_package_version is not None:
        lock["packages"][""]["version"] = lock_package_version
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))

    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_matching_versions_passes(tmp_path):
    result = _run(tmp_path, "1.2.3", "1.2.3")
    assert result.returncode == 0


def test_mismatched_versions_fails(tmp_path):
    result = _run(tmp_path, "1.2.3", "1.2.4")
    assert result.returncode == 1
    assert "Version mismatch" in result.stdout
    assert "pyproject.toml=1.2.3" in result.stdout
    assert "package.json=1.2.4" in result.stdout


def test_missing_pyproject_version_fails(tmp_path):
    result = _run(tmp_path, None, "1.0.0")
    assert result.returncode == 1
    assert "no version field found in pyproject.toml" in result.stdout


def test_missing_package_json_version_fails(tmp_path):
    result = _run(tmp_path, "1.0.0", None)
    assert result.returncode == 1
    assert "no version field found in package.json" in result.stdout


def test_mismatched_client_version_fails(tmp_path):
    result = _run(tmp_path, "1.2.3", "1.2.3", client_version="1.2.2")
    assert result.returncode == 1
    assert "Version mismatch" in result.stdout
    assert "clients/python/pyproject.toml=1.2.2" in result.stdout
    # Stamped by a script, not edited by hand: the message names the script.
    assert "uv run python -m scripts.regenerate_client" in result.stdout


def test_app_mismatch_does_not_suggest_regeneration(tmp_path):
    result = _run(tmp_path, "1.2.3", "1.2.4", client_version="1.2.3")
    assert result.returncode == 1
    assert "regenerate_client" not in result.stdout


def test_missing_client_version_fails(tmp_path):
    result = _run(tmp_path, "1.0.0", "1.0.0", client_version=None)
    assert result.returncode == 1
    assert "no version field found in clients/python/pyproject.toml" in result.stdout


def test_mismatched_lockfile_root_version_fails(tmp_path):
    result = _run(tmp_path, "1.2.3", "1.2.3", lock_version="0.31.0", lock_package_version="1.2.3")
    assert result.returncode == 1
    assert "package-lock.json=0.31.0" in result.stdout
    # Regenerated, not edited: the message names the command that stamps it.
    assert "npm install --package-lock-only" in result.stdout


def test_mismatched_lockfile_package_version_fails(tmp_path):
    result = _run(tmp_path, "1.2.3", "1.2.3", lock_package_version="0.31.0")
    assert result.returncode == 1
    assert 'package-lock.json packages[""]=0.31.0' in result.stdout


def test_missing_lockfile_version_fails(tmp_path):
    result = _run(tmp_path, "1.0.0", "1.0.0", lock_version=None)
    assert result.returncode == 1
    assert "no version field found in package-lock.json" in result.stdout


def test_repo_lockfile_is_in_sync():
    lock = json.loads((ROOT / "package-lock.json").read_text())
    version = json.loads((ROOT / "package.json").read_text())["version"]
    assert lock["version"] == lock["packages"][""]["version"] == version


def test_hook_runs_when_the_lockfile_changes():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    (hook,) = [h for r in config["repos"] for h in r["hooks"] if h["id"] == "check-version-sync"]
    assert re.search(hook["files"], "package-lock.json")
