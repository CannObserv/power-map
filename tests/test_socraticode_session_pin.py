"""Guards the SocratiCode session pin (#559, gregoryfoster/skills#327/#332).

#537 pinned the driver (`~/.socraticode/pin`: the health hook, `index`,
`verify`) and documented the session as unpinnable, because Claude Code cannot
override a plugin's MCP command. Upstream SocratiCode `0c33776` refuted that:
the plugin's live manifest launches `${SOCRATICODE_SPEC:-socraticode@latest}`.
Left unset, every session start runs `@latest` through npx — an install on any
day the package moves, the memory peak #537 pinned the driver to avoid, and a
second server version writing the store the driver reads.

What reaches the launch is Claude Code's environment when it starts: here the
VS Code machine setting `claudeCode.environmentVariables`. The repo's settings
`env` block is the declared value that preflight compares against, not the
mechanism (skills#332 measured it reaching the server's environment but not its
launch). Neither is visible to a test: the daily health hook reads what
actually launched off the process table, so this file holds the repo's half —
the declared value is an exact version, and the docs name that version, the
variable and the machine setting instead of the refuted limitation.
"""

import json
import re
from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
SOCRATICODE_MD = REPO_ROOT / "docs" / "SOCRATICODE.md"

# skills#331-#333 merge: preflight and health-check read the session's pin off
# the process table, and host-memory.md names the machine setting (#332).
SESSION_PIN_COMMIT = "15bfec5"
EXACT_SPEC = re.compile(r"socraticode@(\d+\.\d+\.\d+)")


def declared_spec() -> str:
    """`SOCRATICODE_SPEC` from the committed settings `env` block."""
    return json.loads(SETTINGS.read_text()).get("env", {}).get("SOCRATICODE_SPEC", "")


def repo_notes() -> str:
    """docs/SOCRATICODE.md below the template's END marker — the repo-authored part."""
    text = SOCRATICODE_MD.read_text()
    return text[text.index("<!-- END socraticode-doc -->") :]


@pytest.mark.skipif(not vendor_skills.vendor_skills_present(), reason=vendor_skills.SKIP_REASON)
def test_the_pin_reads_the_session_pin_off_the_process_table() -> None:
    contains = vendor_skills.contains_commit(SESSION_PIN_COMMIT)
    if contains is None:
        pytest.skip(f"git cannot resolve {SESSION_PIN_COMMIT} — ancestry unverifiable")
    assert contains, (
        f"skills-vendor/gregoryfoster-skills predates {SESSION_PIN_COMMIT} (skills#327/#332): "
        "preflight and the health hook cannot see whether the session is pinned"
    )


def test_the_declared_session_spec_is_an_exact_version() -> None:
    spec = declared_spec()
    assert EXACT_SPEC.fullmatch(spec), (
        f"SOCRATICODE_SPEC={spec!r} in .claude/settings.json is not an exact "
        "socraticode@X.Y.Z — a floating spec installs at session start"
    )


def test_the_docs_pin_driver_and_session_to_the_declared_version() -> None:
    version = EXACT_SPEC.fullmatch(declared_spec()).group(1)
    notes = repo_notes()

    assert f"`~/.socraticode/pin` holds `socraticode@{version}`" in notes
    assert f"SOCRATICODE_SPEC=socraticode@{version}" in notes
    assert "claudeCode.environmentVariables" in notes
    assert ".vscode-server/data/Machine/settings.json" in notes


def test_the_docs_no_longer_call_the_session_unpinnable() -> None:
    stale = re.compile(r"does not pin the session|cannot override a plugin", re.I)
    assert not stale.search(repo_notes())
