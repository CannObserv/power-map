"""Tests for scripts/check_version_sync.sh."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_version_sync.sh"


def _run(
    tmp_path: Path,
    py_version: str | None,
    js_version: str | None,
) -> subprocess.CompletedProcess:
    if py_version is not None:
        (tmp_path / "pyproject.toml").write_text(f'version = "{py_version}"\n')
    else:
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'x'\n")

    if js_version is not None:
        (tmp_path / "package.json").write_text(f'{{"name": "x", "version": "{js_version}"}}\n')
    else:
        (tmp_path / "package.json").write_text('{"name": "x"}\n')

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
