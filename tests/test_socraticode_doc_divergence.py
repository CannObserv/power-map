"""Guards the deliberate local divergences in `docs/SOCRATICODE.md`.

That file is **generated**: `init-socraticode` overwrites it wholesale on every
audit re-run, and it carries no marker pair of its own because it *is* the
policy block's overflow. Two sections in it are local corrections that the
upstream template does not yet make, so a re-run would silently revert both:

1. **`unresolvedPct`** (gregoryfoster/skills#198) — the template never explains
   it, so consumers read the daily hook finding as a defect. A sibling repo did
   exactly that and distrusted a correct tool for weeks.
2. **The 12-tool prefetch** (gregoryfoster/skills#209) — the vendored hook loads
   9 and omits three graph tools this project's own table recommends.

These tests are **meant to be deleted.** When the upstream template carries each
explanation itself, drop the corresponding block from `docs/SOCRATICODE.md` and
the test that guards it — that is the retirement condition, not an accident. The
point is that a regeneration is *detectable* rather than silent; a permanent
local fork of a generated file is the failure mode this is built to avoid.

Content checks, not just marker presence: markers wrapped around nothing would
otherwise pass, which is precisely what a wholesale overwrite leaves behind.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "SOCRATICODE.md"

RETIREMENT_HINT = (
    "If gregoryfoster/skills#{issue} has landed the explanation upstream, delete "
    "the block AND this test — that is the intended retirement. If it has not, "
    "an init-socraticode re-run has silently reverted the correction; restore it."
)


@pytest.fixture(scope="module")
def doc() -> str:
    """The generated SocratiCode reference doc."""
    assert DOC_PATH.is_file(), f"missing {DOC_PATH.relative_to(REPO_ROOT)}"
    return DOC_PATH.read_text()


def _block(doc: str, label: str) -> str:
    """The text between the named divergence markers, or '' if absent."""
    begin = f"<!-- BEGIN local-divergence: {label}"
    end = "<!-- END local-divergence -->"
    if begin not in doc:
        return ""
    after = doc.split(begin, 1)[1]
    return after.split(end, 1)[0] if end in after else ""


def test_unresolved_pct_block_survives_regeneration(doc: str) -> None:
    """The unresolvedPct correction is present and still explains itself."""
    block = _block(doc, "unresolvedPct")
    assert block, "unresolvedPct divergence block is gone. " + RETIREMENT_HINT.format(issue=198)

    lowered = block.lower()
    for concept, needle in (
        ("that it counts *call* edges", "call"),
        ("that it is corroboration, not a verdict", "corroboration"),
        ("that the verdict is what to judge on", "verdict"),
        ("the differential test against rg", "rg"),
    ):
        assert needle in lowered, (
            f"the unresolvedPct block no longer explains {concept} — markers "
            "around gutted content still pass a presence check, so this asserts "
            "the substance. " + RETIREMENT_HINT.format(issue=198)
        )


def test_prefetch_block_survives_regeneration(doc: str) -> None:
    """The 12-vs-9 prefetch divergence is present and names its three tools."""
    block = _block(doc, "12-tool prefetch")
    assert block, "prefetch divergence block is gone. " + RETIREMENT_HINT.format(issue=209)

    for tool in (
        "codebase_graph_circular",
        "codebase_graph_stats",
        "codebase_graph_visualize",
    ):
        assert tool in block, (
            f"the prefetch divergence block no longer names {tool}, one of the "
            "three tools the vendored 9-tool hook omits. " + RETIREMENT_HINT.format(issue=209)
        )


def test_both_blocks_name_their_retirement_condition(doc: str) -> None:
    """Each block points at the issue that retires it, so neither becomes permanent."""
    for label, issue in (("unresolvedPct", 198), ("12-tool prefetch", 209)):
        block = _block(doc, label)
        assert f"skills/issues/{issue}" in block, (
            f"the {label} block no longer names gregoryfoster/skills#{issue} as "
            "its retirement condition. A divergence with no stated end becomes a "
            "permanent fork of a generated file."
        )


def test_prefetch_string_matches_the_documented_tool_count(doc: str) -> None:
    """The `select:` string actually carries the 12 tools the block claims."""
    select_lines = [line for line in doc.splitlines() if line.strip().startswith("`select:")]
    assert len(select_lines) == 1, f"expected exactly one select: string, got {len(select_lines)}"

    count = select_lines[0].count("mcp__plugin_socraticode_socraticode__codebase_")
    assert count == 12, (
        f"the prefetch string lists {count} tools but the divergence block claims "
        "12. Update both together, or the doc contradicts itself the way the "
        "upstream template does (gregoryfoster/skills#209)."
    )
