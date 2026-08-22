"""Guards the declared-vs-indexed parity check in the vendored SocratiCode driver.

This is the ratchet that made `docs/SOCRATICODE.md`'s `resolve-but-unindexed`
divergence block retirable (#461, closing the loop on #454/#455).

The gap it protects: `codebase_context_search` answers from *indexed* artifacts
only. An artifact that the manifest **declares** but the server never indexed
produces no error and no warning — the search simply answers without it. In the
field case (#454) a 2.5 MB docs tree sat unreachable behind a *completed*
operation, a non-INCOMPLETE index and three green containers. Every other check
in `health-check` reads that install as healthy.

`gregoryfoster/skills#214` closed it upstream: `mcp-driver.mjs health-check`
compares the manifest's declared count against per-artifact status from
`codebase_context` and reports the shortfall **by name**, exiting 1 like every
other finding so the once-per-day hook surfaces it.

Two things have to stay true for that to reach this repo, and both are pins a
future change can silently move: the vendored submodule has to be at a commit
that carries the check, and `socraticode-health.sh` has to be the thing that
runs it. These tests assert the vendored source statically — no MCP server, no
Qdrant, no network — so a submodule rollback past `2d0f4f5` fails here rather
than reopening the silent gap.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "skills-vendor" / "gregoryfoster-skills"
DRIVER_PATH = VENDOR / "skills" / "init-socraticode" / "scripts" / "mcp-driver.mjs"
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "socraticode-health.sh"

ROLLBACK_HINT = (
    "The vendored gregoryfoster/skills pin no longer carries the declared-vs-indexed "
    "parity check (skills#214, merged 2d0f4f5). A pin that predates it re-opens the "
    "silent gap from #454: bump skills-vendor/gregoryfoster-skills forward, or if the "
    "check genuinely moved upstream, restore the resolve-but-unindexed divergence "
    "block in docs/SOCRATICODE.md that this check retired."
)


@pytest.fixture(scope="module")
def driver() -> str:
    """The vendored `mcp-driver.mjs` source."""
    assert DRIVER_PATH.is_file(), (
        f"missing {DRIVER_PATH.relative_to(REPO_ROOT)} — the submodule is "
        "uninitialized; run `git submodule update --init skills-vendor/`"
    )
    return DRIVER_PATH.read_text()


def test_health_check_reports_the_declared_vs_indexed_gap(driver: str) -> None:
    """`health-check` emits a finding when fewer artifacts are indexed than declared."""
    assert "context artifacts ${indexed}/${declared} indexed" in driver, ROLLBACK_HINT


def test_the_manifest_is_the_denominator(driver: str) -> None:
    """Parity is measured against the manifest, never the server's own total.

    `parseArtifacts` reports `0/0` both for "N declared, none indexed yet" and
    for a status line the server omitted entirely, so the status total cannot
    distinguish "nothing declared" from "nothing indexed". Only the manifest can.
    """
    assert "validateManifest(projectPath)" in driver, ROLLBACK_HINT
    assert "const declared = manifest.count" in driver, ROLLBACK_HINT


def test_the_shortfall_is_named_not_just_counted(driver: str) -> None:
    """The finding names the missing artifact, per `codebase_context` status.

    A bare `2/3` sends the reader back to `codebase_status`; the name is what
    decides between re-indexing one path and debugging the manifest.
    """
    assert "parseContextArtifacts(" in driver, ROLLBACK_HINT
    assert "unindexed.map(" in driver, ROLLBACK_HINT


def test_a_finding_makes_health_check_exit_nonzero(driver: str) -> None:
    """The parity finding surfaces through the hook, which keys on the exit code."""
    assert "process.exitCode = 1" in driver, (
        "health-check no longer exits non-zero on findings, so the daily hook "
        "would log the parity gap and stay silent. " + ROLLBACK_HINT
    )


def test_the_daily_hook_runs_the_vendored_driver() -> None:
    """`socraticode-health.sh` invokes `health-check`, resolving into the submodule.

    The check reaching this repo is a two-part pin: the submodule commit *and*
    the hook that runs it. `tests/sh/claude_hooks.bats` asserts the hook is
    registered and its symlink resolves; this asserts what it actually calls.
    """
    assert HOOK_PATH.is_file(), f"missing {HOOK_PATH.relative_to(REPO_ROOT)}"
    hook = HOOK_PATH.read_text()

    assert "health-check" in hook, "the health hook no longer runs `mcp-driver.mjs health-check`"
    assert "skills-vendor" in hook, (
        "the health hook no longer searches skills-vendor/ for mcp-driver.mjs, so "
        "it may run a stale copy that predates the parity check"
    )
    assert HOOK_PATH.resolve().is_relative_to(VENDOR.resolve()), (
        "the health hook is no longer a symlink into the vendored skill, so a "
        "submodule bump would not carry upstream fixes to it"
    )
