"""Guards `docs/SOCRATICODE.md` **agreeing** with the pinned upstream template.

Successor to `tests/test_socraticode_doc_divergence.py`, deleted in #463. That
file guarded three deliberate local corrections to a *generated* file, each one
carrying a named retirement condition — *when the template carries the
explanation itself*. All three conditions are now met, so all three blocks are
gone and the file below asserts the opposite property: the doc says what the
pinned template says, and nothing local.

The retirements, and where each one's ratchet went:

1. **resolve-but-unindexed** (skills#214), retired in #461. Prose moved to
   `docs/SKILLS.md` § Context artifacts; `tests/test_socraticode_health_parity.py`
   pins the vendored driver that carries the check.
2. **`unresolvedPct`** (skills#198 + skills#216), retired here. The template's
   own § Graph health now explains the figure, and skills#216 reworded the daily
   finding so it says `verdict is ok, so this is a statistic, not a defect` in
   the hook output itself. The repo's measured reading of the line lives in
   `docs/SKILLS.md` § Reading the daily `unresolved N%` line.
3. **The 12-tool prefetch** (skills#209), retired here. The vendored
   `socraticode-reminder.sh` now prefetches every tool the doc's table
   recommends, so `.claude/hooks/socraticode-reminder.sh` became a symlink into
   the submodule — the shape `socraticode-health.sh` has had since #179 — rather
   than the hand-authored copy that carried the three missing tools.

**A deletion is only a retirement if something still fails when the pin moves
backwards.** That is what this file is: each retired block leaves behind an
ancestry assertion on the submodule commit that made it retirable, plus the
doc-to-hook pair-check that survived the retirement unchanged. A rollback past
any of them reds a test instead of silently restoring a gap nobody notices —
`docs/SOCRATICODE.md` is regenerated, so silence is its default failure mode.

#463 also adopted the template's `<!-- BEGIN socraticode-doc -->` /
`<!-- END socraticode-doc -->` pair (skills#210), which turns a re-run from a
whole-file overwrite into a bounded replace. That is what makes the assertions
here two-sided: the span must match the pinned template, and everything
repo-authored must sit below `END`, where a re-run does not reach.

**Ancestry is the assertion; source strings are corroboration** — the same rule
`test_socraticode_health_parity.py` states at length. The vendored files may not
be edited here, so an upstream refactor can reflow any of the strings below with
no behavioural change; only `git merge-base --is-ancestor` is immune to that.
"""

import difflib
import re
from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = vendor_skills.VENDOR_ROOT
SKILL_DIR = VENDOR / "skills" / "init-socraticode"
VENDOR_HOOK = SKILL_DIR / "scripts" / "socraticode-reminder.sh"
TEMPLATE_PATH = SKILL_DIR / "references" / "socraticode-doc.md"

DOC_PATH = REPO_ROOT / "docs" / "SOCRATICODE.md"
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "socraticode-reminder.sh"

TOOL_RE = re.compile(r"mcp__plugin_socraticode_socraticode__(codebase_[a-z_]+)")
TABLE_TOOL_RE = re.compile(r"`(codebase_[a-z_]+)`")

# `#209 fix: prefetch every tool the doc's table recommends` — the commit that
# made the hand-authored 12-tool copy unnecessary.
PREFETCH_COMMIT = "96c1541"
# `#210 fix: give the generated SOCRATICODE.md a repo-specific region` — the
# marker pair that makes a re-run a bounded replace instead of a whole-file one.
MARKER_COMMIT = "708afeb"
BEGIN_MARKER = "<!-- BEGIN socraticode-doc -->"
END_MARKER = "<!-- END socraticode-doc -->"

# `#198 docs: explain unresolvedPct in the doc template's Graph health section`.
UNRESOLVED_DOC_COMMIT = "1e76baf"
# `#216 fix: word the unresolvedPct finding from the verdict` — the daily hook
# line the divergence block existed to translate.
UNRESOLVED_WORDING_COMMIT = "e216fc2"

DRIVER_PATH = vendor_skills.DRIVER_PATH

vendor_only = pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)

REFACTOR_HINT = (
    "The pin contains the commit, so this is most likely an upstream refactor — "
    "a rename or a reflow — rather than a lost feature. Confirm against the "
    "vendored file and re-anchor this assertion; do NOT read it as a rollback, "
    "and do not edit skills-vendor/ to make it pass."
)


def _rollback_hint(commit: str, issue: int, what: str) -> str:
    """Message for an ancestry check that answered *no*."""
    return (
        f"The vendored gregoryfoster/skills pin does not contain {commit} "
        f"(skills#{issue}), so {what}. Bump skills-vendor/gregoryfoster-skills "
        "forward, or restore the corresponding local-divergence block in "
        "docs/SOCRATICODE.md and its guard here (see #463 for both texts)."
    )


def _assert_pin_contains(commit: str, issue: int, what: str) -> None:
    """Ancestry gate shared by the three retirement ratchets."""
    contains = vendor_skills.contains_commit(commit)
    if contains is None:
        pytest.skip(
            f"git cannot resolve {commit} in {VENDOR.name} (shallow clone or "
            "missing git) — ancestry unverifiable, so it is not reported either way"
        )
    assert contains, _rollback_hint(commit, issue, what)


@pytest.fixture(scope="module")
def doc() -> str:
    """The generated SocratiCode reference doc."""
    assert DOC_PATH.is_file(), f"missing {DOC_PATH.relative_to(REPO_ROOT)}"
    return DOC_PATH.read_text()


@pytest.fixture(scope="module")
def hook() -> str:
    """The reminder hook as installed — which is now the vendored file itself."""
    if HOOK_PATH.is_symlink() and not HOOK_PATH.exists():
        pytest.fail(
            f"{HOOK_PATH.relative_to(REPO_ROOT)} is a dangling symlink -> "
            f"{HOOK_PATH.readlink()}; the hook is installed but its target is "
            f"absent. Run: {vendor_skills.INIT_HINT}"
        )
    assert HOOK_PATH.is_file(), f"missing {HOOK_PATH.relative_to(REPO_ROOT)}"
    return HOOK_PATH.read_text()


def _span(doc: str) -> str:
    """Everything the template owns: `BEGIN` through `END`, inclusive.

    Fails rather than raising `ValueError` when the markers are missing: an
    unmarked file is the pre-#210 shape and a real finding, and it reaches
    every caller here, so each one should say what it means instead of dying
    on a bare substring lookup.
    """
    if BEGIN_MARKER not in doc or END_MARKER not in doc:
        pytest.fail(
            "docs/SOCRATICODE.md has no socraticode-doc marker pair, so an "
            "init-socraticode re-run takes the whole-file branch and everything "
            "below `## Repo-specific notes` goes with it (skills#210). Restore "
            f"{BEGIN_MARKER} above the title and {END_MARKER} at the foot of "
            "the generated half."
        )
    return doc[doc.index(BEGIN_MARKER) : doc.index(END_MARKER) + len(END_MARKER)]


def _tool_table(text: str) -> str:
    """The *When to use each tool* table, header row through blank line."""
    start = text.index("| Goal | Tool |")
    return text[start : text.index("\n\n", start) + 1]


def _template_span() -> str:
    """The pinned template's generated span, unwrapped from its fence."""
    body = TEMPLATE_PATH.read_text()
    return body.split("````markdown\n", 1)[1].split("\n````", 1)[0]


def _table_tools(doc: str) -> set[str]:
    """Every `codebase_*` tool named in the doc's *When to use each tool* table.

    Table rows only. The prose elsewhere names tools the prefetch deliberately
    omits (`codebase_index`, `codebase_context_index`, `codebase_graph_status`),
    which are run by hand or by the driver, never mid-exploration.
    """
    rows = [line for line in doc.splitlines() if line.startswith("|")]
    return {tool for row in rows for tool in TABLE_TOOL_RE.findall(row)}


def _select_tools(doc: str) -> set[str]:
    """The tools listed in the doc's single `select:` prefetch string."""
    select_lines = [line for line in doc.splitlines() if line.strip().startswith("`select:")]
    assert len(select_lines) == 1, f"expected exactly one select: string, got {len(select_lines)}"
    return set(TOOL_RE.findall(select_lines[0]))


def test_the_generated_doc_carries_no_local_divergences(doc: str) -> None:
    """Every `local-divergence` block is retired, so a re-run cannot revert one.

    The inverse of what the deleted guard asserted. A block reappearing is not
    itself wrong — but it is a local fork of a generated file, and it must come
    back with a stated retirement condition and a guard, not on its own.

    Anchored on the opening marker rather than the bare string: the doc's own
    header explains that no blocks remain, and naming the mechanism it is free
    of must not read as carrying one.
    """
    assert "<!-- BEGIN local-divergence" not in doc, (
        "docs/SOCRATICODE.md carries a local-divergence block again. All three "
        "were retired (#461, #463) once the upstream template carried each "
        "explanation itself. If a new divergence is genuinely needed, it must "
        "name the upstream issue that retires it and be guarded here — an "
        "unguarded one is silently erased by the next init-socraticode re-run."
    )


# ── The unresolvedPct block (skills#198, skills#216) ─────────────────────────


@vendor_only
def test_the_pin_carries_the_unresolvedpct_explanation() -> None:
    """The load-bearing ratchet for retirement 2: the template explains the figure."""
    _assert_pin_contains(
        UNRESOLVED_DOC_COMMIT,
        198,
        "the doc template does not explain `unresolvedPct` and a regenerated "
        "docs/SOCRATICODE.md would drop the explanation entirely",
    )


@vendor_only
def test_the_template_says_unresolvedpct_is_not_a_verdict() -> None:
    """Corroboration: the pinned template carries the sentence the block carried."""
    template = TEMPLATE_PATH.read_text()
    assert "corroboration, not a verdict" in template, REFACTOR_HINT
    assert "edges/file" in template, REFACTOR_HINT


@vendor_only
def test_the_daily_finding_says_a_healthy_graph_is_not_a_defect() -> None:
    """The hook output itself now says what the block was written to say.

    skills#216 words the finding from the verdict: beside `ok` there is nothing
    for the figure to corroborate, so it reports a statistic. Reading the daily
    line as a defect is the failure mode that cost a sibling repo weeks, and
    with this wording the reader no longer has to find a doc to avoid it.
    """
    _assert_pin_contains(
        UNRESOLVED_WORDING_COMMIT,
        216,
        "the daily health hook still reports `corroborates a resolver problem` "
        "beside `verdict: ok`, which reads as a defect",
    )
    assert "statistic, not a defect" in DRIVER_PATH.read_text(), REFACTOR_HINT


def test_the_doc_still_explains_the_figure_it_stopped_correcting(doc: str) -> None:
    """Retiring the block replaced local prose with the template's, not with nothing.

    The block was deleted because the template carries the explanation — so the
    generated file must actually carry it. A deletion that also dropped the
    template's paragraphs would leave the doc worse than the divergence did.
    """
    lowered = doc.lower()
    for concept, needle in (
        ("that it is corroboration, not a verdict", "corroboration, not a verdict"),
        ("that it counts call edges", "call edges"),
        ("that edges/file is what the gate keys on", "edges/file"),
        ("the differential test against rg", "`rg`"),
    ):
        assert needle in lowered, (
            f"docs/SOCRATICODE.md no longer explains {concept}. The unresolvedPct "
            "divergence block was retired (#463) on the grounds that the template "
            "carries this itself — if a re-run produced a doc without it, the "
            "grounds are gone: restore the block and its guard."
        )


# ── The 12-tool prefetch block (skills#209) ──────────────────────────────────


@vendor_only
def test_the_pin_carries_the_superset_prefetch() -> None:
    """The load-bearing ratchet for retirement 3: the vendored hook prefetches all 12."""
    _assert_pin_contains(
        PREFETCH_COMMIT,
        209,
        "the vendored reminder hook prefetches 9 tools and omits three the doc's "
        "table recommends, so an agent following that table gets "
        "InputValidationError — the exact failure the prefetch prevents",
    )


@vendor_only
def test_the_reminder_hook_is_the_vendored_one() -> None:
    """`.claude/hooks/socraticode-reminder.sh` is a symlink into the submodule.

    The hand-authored copy existed only because the vendored hook was short
    three tools; with #209 landed there is nothing left to fork, and a symlink
    means the next prefetch change upstream arrives on the normal submodule
    refresh instead of by someone noticing. The sibling `socraticode-health.sh`
    has been installed this way since #179.
    """
    assert HOOK_PATH.is_symlink(), (
        f"{HOOK_PATH.relative_to(REPO_ROOT)} is a regular file again. A copy "
        "freezes at install day and `.skills/doctor.sh` — which scans for "
        "dangling symlinks — can never see that it went stale. Reinstall with "
        "managing-skills/scripts/install-hook.sh rather than retyping it."
    )
    assert HOOK_PATH.resolve() == VENDOR_HOOK.resolve(), (
        f"{HOOK_PATH.relative_to(REPO_ROOT)} resolves to {HOOK_PATH.resolve()}, "
        f"not to the vendored {VENDOR_HOOK.relative_to(REPO_ROOT)}"
    )


def test_doc_prefetch_matches_the_hook_that_actually_runs(doc: str, hook: str) -> None:
    """The doc's `select:` string lists exactly what the reminder hook emits.

    This pair-guard predates the retirement and survives it unchanged — it was
    written for exactly this move, so that switching the hook without updating
    the doc could not leave the doc asserting a prefetch nobody runs. It is now
    also the cheapest rollback detector of the three: a submodule rolled back
    past skills#209 changes the hook's tool list under a doc that still lists 12.
    """
    hook_tools = set(TOOL_RE.findall(hook))
    doc_tools = _select_tools(doc)

    assert hook_tools, "no codebase_* tools found in the reminder hook"
    assert hook_tools == doc_tools, (
        "the reminder hook and docs/SOCRATICODE.md disagree about the prefetch.\n"
        f"  only in the hook: {sorted(hook_tools - doc_tools) or 'none'}\n"
        f"  only in the doc:  {sorted(doc_tools - hook_tools) or 'none'}\n"
        "The hook is a symlink into skills-vendor/, so a difference here is "
        "either a submodule move that changed the prefetch (update the doc's "
        "select: string to match) or a rollback past skills#209 (bump forward)."
    )


def test_the_prefetch_covers_every_tool_the_doc_table_recommends(doc: str) -> None:
    """Nothing in the tool table is missing from the prefetch that loads it.

    The divergence existed because the table and the prefetch disagreed: an
    agent handed the table called a tool whose schema was never loaded. The
    property that failure violated outlives the block that documented it, so it
    is asserted directly rather than through a tool count — a count passes when
    twelve tools are listed and one of them is the wrong twelve.
    """
    missing = _table_tools(doc) - _select_tools(doc)
    assert not missing, (
        f"the doc's tool table recommends {sorted(missing)}, which the prefetch "
        "does not load — an agent following the table it was just handed gets "
        "InputValidationError. Add them to the select: string, or drop the rows."
    )


# ── The generated span, and what survives a re-run (skills#210) ──────────────


@vendor_only
def test_the_pin_carries_the_marker_pair_convention() -> None:
    """The ratchet for the bounded-replace shape: the template emits the markers."""
    _assert_pin_contains(
        MARKER_COMMIT,
        210,
        "the template has no marker pair, so a re-run replaces docs/SOCRATICODE.md "
        "wholesale and the repo-specific notes below the END marker go with it",
    )


def test_the_doc_is_marker_delimited(doc: str) -> None:
    """One `BEGIN`, one `END`, in that order, each alone on its line.

    Unmarked is the pre-#210 shape and the dangerous one: `init-socraticode`
    then takes the whole-file branch, and everything repo-authored in the file
    is gone with no diff to notice. Duplicated or reordered markers are worse
    than absent — the replace lands on the wrong span.
    """
    assert doc.count(BEGIN_MARKER) == 1, f"expected exactly one {BEGIN_MARKER}"
    assert doc.count(END_MARKER) == 1, f"expected exactly one {END_MARKER}"
    assert doc.index(BEGIN_MARKER) < doc.index(END_MARKER), "END marker precedes BEGIN"
    for marker in (BEGIN_MARKER, END_MARKER):
        assert f"\n{marker}\n" in f"\n{doc}", (
            f"{marker} shares its line with other text; the skill matches it "
            "unbroken on a line of its own"
        )


def test_the_repo_authored_notes_are_below_the_end_marker(doc: str) -> None:
    """Everything a re-run would eat lives where a re-run does not reach.

    This is the property the marker pair buys, and it is worth asserting
    directly rather than trusting placement: the notes are the third home this
    content has had (`AGENTS.md`, then a guarded divergence block, now here),
    and each move was made because the previous one lost it.
    """
    span = _span(doc)
    below = doc.split(END_MARKER, 1)[1]

    # As a heading on its own line: the template's header *names* the heading in
    # prose, so a substring search finds it inside the span and reads as a pass.
    assert "\n## Repo-specific notes\n" in below, (
        "no `## Repo-specific notes` heading below the END marker. Either the "
        "section is gone, or it sits inside the templated span — where the next "
        "init-socraticode re-run overwrites it."
    )
    for token, what in (
        ("#360", "the .socraticodeignore rationale"),
        ("SKILLS.md", "the pointer to the repo-authored measurements"),
        ("test_socraticode_doc_parity", "the reference to this guard"),
    ):
        assert token not in span, (
            f"{what} ({token}) is inside the templated span. Move it below the "
            f"{END_MARKER} line — a re-run replaces the span and would drop it."
        )


@vendor_only
def test_the_span_is_the_pinned_template_apart_from_the_tool_table(doc: str) -> None:
    """The generated half says what the pin says, to the line.

    The tool table is excluded because the template's own adaptation checklist
    asks for it: the rows must name the tools this server build exposes and
    this project's tree. Nothing else in the span is this repo's to write —
    that is what "generated" means, and it is the claim every retirement in
    this file rests on.
    """
    template = _template_span()
    expected = template.replace(_tool_table(template), _tool_table(doc))
    if _span(doc) == expected:
        return

    diff = "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            _span(doc).splitlines(),
            "pinned template",
            "docs/SOCRATICODE.md span",
            lineterm="",
            n=1,
        )
    )
    pytest.fail(
        "docs/SOCRATICODE.md has drifted from the template at the current pin.\n"
        "Neither side is wrong by default: an upstream edit to the template "
        "means the doc is stale and should be regenerated from it, while a "
        "local edit inside the span means the content belongs below the "
        f"{END_MARKER} line instead. Do not resolve it by editing "
        f"skills-vendor/.\n\n{diff}"
    )
