"""Pin how many PRs `.github/dependabot.yml` can open, where each one is a CI run (#727).

`test_dependabot.py` is the adopting-dependabot skill's asset, edited only in
its ADAPT block. This file adds power-map's own rules for the volume.

Without a config, Dependabot opened security PRs in five rounds over six days
(2026-10-26 to 10-31), one PR per advisory and 16 in all (#715, #729, #736).
Each round was landed by hand as a single lock change, and every PR was then
closed. So security updates are grouped into one PR per ecosystem. Version
updates run weekly, minor and patch bumps grouped, and majors stay individual
PRs, because a major is the bump that needs its changelog read (starlette
0.52 → 1.3, #722). The exception is LOCKSTEP: packages released as a set, whose
majors fail apart (#727's first batch: @eslint/js 10 alone, `npm ci` ERESOLVE).
"""

import fnmatch
import json
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
PACKAGE_BLOCKS = ("uv", "npm")
# Groups that carry majors too, because their members peer-depend on one
# version: (ecosystem, group) -> patterns. @eslint/js N needs eslint N.
LOCKSTEP = {("npm", "eslint"): ["eslint", "@eslint/*"]}


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
        if (ecosystem, name) in LOCKSTEP:
            continue
        assert sorted(group.get("update-types", [])) == ["minor", "patch"], (
            f"{ecosystem}.{name}: a major grouped with minors holds them all "
            "behind one changelog read"
        )


def _npm_packages() -> set[str]:
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    return set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))


@pytest.mark.parametrize("key", LOCKSTEP, ids=lambda k: f"{k[0]}.{k[1]}")
def test_lockstep_packages_move_in_one_pr(key: tuple[str, str]) -> None:
    """A set released together gets one group with no `update-types`, so its major
    is one PR. Apart, each member's major fails `npm ci` on the other's peer range."""
    ecosystem, name = key
    groups = _version_groups(_block(ecosystem))
    assert name in groups, f"{ecosystem}: no `{name}` group; its majors open apart and fail"
    group = groups[name]
    assert group.get("patterns") == LOCKSTEP[key]
    assert "update-types" not in group, f"{ecosystem}.{name}: majors must move as one PR too"


@pytest.mark.parametrize("key", LOCKSTEP, ids=lambda k: f"{k[0]}.{k[1]}")
def test_lockstep_group_precedes_the_catch_all(key: tuple[str, str]) -> None:
    """Dependabot assigns a dependency to the first group it matches: after `*`,
    the lockstep group is empty and its minors ride the catch-all."""
    ecosystem, name = key
    order = list(_version_groups(_block(ecosystem)))
    catch_alls = [
        n for n, g in _version_groups(_block(ecosystem)).items() if g.get("patterns") == ["*"]
    ]
    assert name in order and all(order.index(name) < order.index(c) for c in catch_alls)


def test_lockstep_patterns_match_real_packages() -> None:
    """A set renamed or dropped leaves a group matching nothing; each needs 2+ members."""
    for (ecosystem, name), patterns in LOCKSTEP.items():
        assert ecosystem == "npm", "only npm's members are derived here"
        members = {p for p in _npm_packages() if any(fnmatch.fnmatch(p, x) for x in patterns)}
        assert len(members) >= 2, f"{name}: matches {sorted(members)}"


def test_minor_and_patch_bumps_have_a_catch_all() -> None:
    """Without a `*` group, a runtime minor bump falls out of every group and opens alone."""
    for ecosystem in PACKAGE_BLOCKS:
        patterns = [g.get("patterns") for g in _version_groups(_block(ecosystem)).values()]
        assert ["*"] in patterns, f"{ecosystem}: no catch-all version group"


def test_npm_open_pr_limit_outlasts_a_full_backlog() -> None:
    """The asset derives uv's bound; npm's is derived the same way from package.json."""
    deps = len(_npm_packages())
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
