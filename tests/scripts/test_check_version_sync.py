"""Tests for scripts/check_version_sync.sh.

Three sites carry the version: pyproject.toml, package.json and, since #618,
clients/python/pyproject.toml (the generated client ships at the app's version).
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_version_sync.sh"
_SAME = object()  # client_version default: whatever pyproject.toml says


def _run(
    tmp_path: Path,
    py_version: str | None,
    js_version: str | None,
    client_version: object = _SAME,
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


def test_missing_client_version_fails(tmp_path):
    result = _run(tmp_path, "1.0.0", "1.0.0", client_version=None)
    assert result.returncode == 1
    assert "no version field found in clients/python/pyproject.toml" in result.stdout
