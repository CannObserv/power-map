"""Pin how many PRs `.github/dependabot.yml` can open, where each one is a CI run (#727).

`test_dependabot.py` is the adopting-dependabot skill's asset, edited only in
its ADAPT block. This file adds power-map's own rules for the volume.

Without a config, Dependabot opened security PRs in five rounds over six days
(2026-10-26 to 10-31), one PR per advisory and 16 in all (#715, #729, #736).
Each round was landed by hand as a single lock change, and every PR was then
closed. So security updates are grouped into one PR per ecosystem. Version
updates run weekly, minor and patch bumps grouped, and majors stay individual
PRs, because a major is the bump that needs its changelog read (starlette
0.52 → 1.3, #722).
"""

import json
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
PACKAGE_BLOCKS = ("uv", "npm")


def _updates() -> list[dict]:
    """The update blocks, or [] while the config is missing (test_dependabot.py fails that)."""
    if not CONFIG.is_file():
        return []
    return (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("updates") or []


def _block(ecosystem: str) -> dict:
    (block,) = [u for u in _updates() if u["package-ecosystem"] == ecosystem]
    return block


def _version_groups(block: dict) -> dict[str, dict]:
    return {
        name: group
        for name, group in (block.get("groups") or {}).items()
        if group.get("applies-to", "version-updates") == "version-updates"
    }


@pytest.mark.parametrize("block", _updates(), ids=lambda b: b["package-ecosystem"])
def test_every_block_runs_weekly(block: dict) -> None:
    assert block["schedule"]["interval"] == "weekly"


@pytest.mark.parametrize("ecosystem", PACKAGE_BLOCKS)
def test_security_updates_are_one_pr_per_ecosystem(ecosystem: str) -> None:
    security = [
        g
        for g in (_block(ecosystem).get("groups") or {}).values()
        if g.get("applies-to") == "security-updates"
    ]
    assert security, f"{ecosystem}: no security-updates group; each advisory opens its own PR"
    assert any(g.get("patterns") == ["*"] for g in security), (
        f"{ecosystem}: the security group must match every package"
    )


@pytest.mark.parametrize("ecosystem", PACKAGE_BLOCKS)
def test_version_groups_leave_majors_individual(ecosystem: str) -> None:
    groups = _version_groups(_block(ecosystem))
    assert groups, f"{ecosystem}: no version-updates group; every bump is its own PR"
    for name, group in groups.items():
        assert sorted(group.get("update-types", [])) == ["minor", "patch"], (
            f"{ecosystem}.{name}: a major grouped with minors holds them all "
            "behind one changelog read"
        )


def test_minor_and_patch_bumps_have_a_catch_all() -> None:
    """Without a `*` group, a runtime minor bump falls out of every group and opens alone."""
    for ecosystem in PACKAGE_BLOCKS:
        patterns = [g.get("patterns") for g in _version_groups(_block(ecosystem)).values()]
        assert ["*"] in patterns, f"{ecosystem}: no catch-all version group"


def test_npm_open_pr_limit_outlasts_a_full_backlog() -> None:
    """The asset derives uv's bound; npm's is derived the same way from package.json."""
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    deps = len(package.get("dependencies", {})) + len(package.get("devDependencies", {}))
    block = _block("npm")
    groups = len(_version_groups(block))
    limit = block.get("open-pull-requests-limit", 5)
    assert limit > deps + groups, f"limit {limit} can be filled by {deps} PRs and {groups} groups"


def _pinned_by_hand() -> set[str]:
    """Every exact (`==`) or git-sourced package in pyproject.toml.

    None of them moves on the bot's schedule."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(data["project"]["dependencies"])
    for group in data.get("dependency-groups", {}).values():
        specs += [s for s in group if isinstance(s, str)]
    exact = {s.split("==")[0].split("[")[0].strip() for s in specs if "==" in s}
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    git = {name for name, src in sources.items() if "git" in src or "url" in src}
    return exact | git


def test_uv_ignores_every_hand_pinned_package() -> None:
    """Derived, so a new `==` pin or git source fails here until the config ignores it."""
    pinned = _pinned_by_hand()
    assert pinned, "found no exact or git pins: the derivation broke"
    ignored = {i["dependency-name"] for i in _block("uv").get("ignore") or []}
    assert pinned <= ignored, f"not ignored: {sorted(pinned - ignored)}"
