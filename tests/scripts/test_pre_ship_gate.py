"""Guards this repo's ship gate: the vendored `pre-ship.sh`, tailored by one file (#549).

`shipping-work-python-fastapi` Step 1 resolves `scripts/<name>.sh` from the repo
root **before** the skill directory. #539 put a copy there because the vendored
gate ran `uv run pytest … -m "not integration"` — that `-m` **replaced** our
`addopts` default of `-m 'not integration and not browser'` and so requested the
browser tier — and because it omitted the `--group seed` our pre-commit hook
passes. Upstream fixed both (skills#304): integration-marked items are now
deselected by a collection plugin, leaving `addopts` alone, and
`.skills/pre-ship-uv-args` puts the project's arguments after `uv run` in every
uv call. The copy's own ratchet fired on the bump that brought that in, and it
was deleted (#549, #463: a divergence retires itself).

What keeps the vendored gate right for this repo:

* `test_no_local_gate_shadows_the_vendored_one` — a `scripts/pre-ship.sh` would
  win Step 1 again and stop receiving upstream fixes without saying so.
* `test_the_vendored_gate_reads_our_uv_args_file` — the knob we depend on must
  still exist, or `.skills/pre-ship-uv-args` is inert and the gate runs a
  narrower suite than the hook.
* `test_the_vendored_gate_leaves_the_marker_alone` — the #539 defect, watched
  upstream now: the day a vendored pytest line passes `-m` again, this fails
  rather than the ship gate silently acquiring the browser tier.
* `test_our_uv_args_match_the_pre_commit_hook` — the ship gate and the pre-commit
  `pytest (unit)` hook run one suite definition.

`tests/sh/pre_ship.bats` runs the vendored gate against our real args file.
"""

import re
import shlex
from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).parents[2]
LOCAL_GATE = REPO_ROOT / "scripts" / "pre-ship.sh"
UV_ARGS_FILE = REPO_ROOT / ".skills" / "pre-ship-uv-args"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

VENDORED_GATE = (
    vendor_skills.VENDOR_ROOT
    / "skills"
    / "shipping-work-python-fastapi"
    / "scripts"
    / "pre-ship.sh"
)

# An executable pytest run through uv, either spelling: `uv run … pytest` or the
# vendored `uv_run pytest`. `\bpytest\b` does not match the `pytest_cov` probe.
PYTEST_RUN = re.compile(r"^\s*(?:[A-Z_]+=\S+\s+)*(?:uv run\b|uv_run\b).*\bpytest\b")


@pytest.fixture(scope="module")
def vendored_source() -> str:
    if not VENDORED_GATE.is_file():
        pytest.skip(vendor_skills.SKIP_REASON)
    return VENDORED_GATE.read_text()


def _uv_args_of(entry: str) -> list[str]:
    """The arguments between `uv run` and `pytest` in a hook's command line."""
    words = shlex.split(entry)
    start = words.index("run") + 1
    return words[start : words.index("pytest")]


def _uv_args_file() -> list[str]:
    """`.skills/pre-ship-uv-args` as the vendored gate reads it: words, `#` lines skipped."""
    return [
        word
        for line in UV_ARGS_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        for word in line.split()
    ]


def test_uv_args_of_reads_the_hook_entry():
    """The parser: everything between `uv run` and `pytest`, nothing after."""
    entry = 'uv run --group seed pytest --no-cov -x -m "not integration and not browser"'
    assert _uv_args_of(entry) == ["--group", "seed"]
    assert _uv_args_of("uv run pytest -x") == []


def test_no_local_gate_shadows_the_vendored_one() -> None:
    """Step 1 probes `scripts/` first: a copy there would stop upstream fixes reaching us."""
    assert not LOCAL_GATE.exists(), (
        f"{LOCAL_GATE.relative_to(REPO_ROOT)} shadows the vendored gate. Tailor it through "
        ".skills/pre-ship-uv-args, or — if upstream regressed — say why here before re-adding "
        "a copy (#539, #549)."
    )


def test_the_vendored_gate_reads_our_uv_args_file(vendored_source: str) -> None:
    """Without the knob, our args file does nothing and the gate runs a narrower suite."""
    assert ".skills/pre-ship-uv-args" in vendored_source, (
        "the vendored gate no longer reads .skills/pre-ship-uv-args — --group seed is lost"
    )


def test_the_vendored_gate_leaves_the_marker_alone(vendored_source: str) -> None:
    """Any `-m` on a pytest command line replaces the `addopts` marker wholesale (#539)."""
    runs = [line for line in vendored_source.splitlines() if PYTEST_RUN.match(line)]
    assert runs, "found no pytest invocation in the vendored gate — re-anchor PYTEST_RUN"
    for line in runs:
        assert not re.search(r"\s-m\s", line), (
            f"{line.strip()!r} passes -m again, which requests the browser tier here. "
            "Restore a project-local gate (see the module docstring) until upstream fixes it."
        )


def test_our_uv_args_match_the_pre_commit_hook() -> None:
    """One suite definition, two gates: the ship gate runs what the hook runs."""
    entry = next(
        (
            line.split("entry:", 1)[1].strip()
            for line in PRE_COMMIT.read_text().splitlines()
            if "entry:" in line and "pytest" in line
        ),
        None,
    )
    assert entry is not None, "no pytest entry found in .pre-commit-config.yaml"
    assert UV_ARGS_FILE.is_file(), f"missing {UV_ARGS_FILE.relative_to(REPO_ROOT)}"
    assert _uv_args_file() == _uv_args_of(entry), (
        f".skills/pre-ship-uv-args gives {_uv_args_file()}, the pre-commit pytest hook "
        f"{_uv_args_of(entry)}: the ship gate would run a different suite from the hook"
    )
