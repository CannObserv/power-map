"""Guards the declared-vs-indexed parity check in the vendored SocratiCode driver.

This is the ratchet that made `docs/SOCRATICODE.md`'s `resolve-but-unindexed`
divergence block retirable (#461, closing the loop on #454/#455). The prose that
block carried now lives in `docs/SKILLS.md` § Context artifacts, which is
repo-authored and never regenerated.

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
runs it.

**Ancestry is the assertion; source strings are corroboration.** The vendored
driver is a file this repo may not edit (`skills-vendor` policy), so an upstream
refactor can rename a variable or reflow a template literal with no behavioural
change at all. A guard built only on substrings would then red with a message
blaming a rollback that did not happen — and this repo has a written case study
(`docs/SKILLS.md` § Reading the daily `unresolved N%` line) of what a confidently
wrong signal costs. So the load-bearing check asks git whether the pin contains
`2d0f4f5`, which no reformatting can disturb; the substring checks stay, but say
"upstream may have refactored" rather than "the pin rolled back".
"""

from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = vendor_skills.VENDOR_ROOT
DRIVER_PATH = vendor_skills.DRIVER_PATH
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "socraticode-health.sh"

# The batch/a merge that landed gregoryfoster/skills#214.
PARITY_COMMIT = "2d0f4f5"

pytestmark = pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)

ROLLBACK_HINT = (
    f"The vendored gregoryfoster/skills pin does not contain {PARITY_COMMIT} "
    "(skills#214), so the declared-vs-indexed parity check is absent and the "
    "silent gap from #454 is re-opened. Bump skills-vendor/gregoryfoster-skills "
    "forward. If the check genuinely moved upstream, restore the "
    "resolve-but-unindexed guidance in docs/SKILLS.md § Context artifacts."
)

REFACTOR_HINT = (
    "The pin contains the parity check, so this is most likely an upstream "
    "refactor — a rename or a reflow — rather than a lost feature. Confirm "
    "against the vendored mcp-driver.mjs and re-anchor this assertion; do NOT "
    "read it as a rollback, and do not edit skills-vendor/ to make it pass."
)


@pytest.fixture(scope="module")
def driver() -> str:
    """The vendored `mcp-driver.mjs` source."""
    return DRIVER_PATH.read_text()


def test_the_pin_contains_the_parity_check() -> None:
    """The load-bearing guard: the submodule is at or past skills#214.

    Immune to upstream reformatting in a way the source-string checks below are
    not, which is why this one owns `ROLLBACK_HINT`.
    """
    contains = vendor_skills.contains_commit(PARITY_COMMIT)
    if contains is None:
        pytest.skip(
            f"git cannot resolve {PARITY_COMMIT} in {VENDOR.name} "
            "(shallow clone or missing git) — ancestry unverifiable, so it is "
            "not reported either way"
        )
    assert contains, ROLLBACK_HINT


def test_health_check_reports_the_declared_vs_indexed_gap(driver: str) -> None:
    """`health-check` emits a finding when fewer artifacts are indexed than declared."""
    assert "context artifacts ${indexed}/${declared} indexed" in driver, REFACTOR_HINT


def test_the_manifest_is_the_denominator(driver: str) -> None:
    """Parity is measured against the manifest, never the server's own total.

    `parseArtifacts` reports `0/0` both for "N declared, none indexed yet" and
    for a status line the server omitted entirely, so the status total cannot
    distinguish "nothing declared" from "nothing indexed". Only the manifest can.
    """
    assert "validateManifest(projectPath)" in driver, REFACTOR_HINT
    assert "manifest.count" in driver, REFACTOR_HINT


def test_the_shortfall_is_named_not_just_counted(driver: str) -> None:
    """The finding names the missing artifact, per `codebase_context` status.

    A bare `2/3` sends the reader back to `codebase_status`; the name is what
    decides between re-indexing one path and debugging the manifest. Anchored on
    the parser call rather than the call site that consumes it — the parser is a
    named export-shaped function, whereas the consuming expression is one
    formatting pass away from being spelled differently.
    """
    assert "parseContextArtifacts(" in driver, REFACTOR_HINT


def test_a_finding_makes_health_check_exit_nonzero(driver: str) -> None:
    """The parity finding surfaces through the hook, which keys on the exit code.

    Ordering, not mere presence: the exit has to come *after* the parity finding
    is pushed, or the check could be reported and still exit 0.
    """
    parity = driver.find("context artifacts ${indexed}/${declared} indexed")
    assert parity != -1, REFACTOR_HINT
    assert "process.exitCode = 1" in driver[parity:], (
        "health-check no longer exits non-zero after collecting the parity "
        "finding, so the daily hook would log the gap and stay silent. " + REFACTOR_HINT
    )


def test_the_daily_hook_runs_the_vendored_driver() -> None:
    """`socraticode-health.sh` invokes `health-check`, resolving into the submodule.

    The check reaching this repo is a two-part pin: the submodule commit *and*
    the hook that runs it. `tests/sh/claude_hooks.bats` asserts the hook is
    registered and its symlink resolves; this asserts what it actually calls.
    """
    # Distinguish absent from dangling: `is_file()` is False for both, and
    # "missing" would send the reader to reinstall a hook that is installed
    # correctly when the real fix is a submodule init.
    if HOOK_PATH.is_symlink() and not HOOK_PATH.exists():
        pytest.fail(
            f"{HOOK_PATH.relative_to(REPO_ROOT)} is a dangling symlink -> "
            f"{HOOK_PATH.readlink()}; the hook is installed but its target is "
            f"absent. Run: {vendor_skills.INIT_HINT}"
        )
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
