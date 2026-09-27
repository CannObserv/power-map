"""needrestart lists restarts and never performs them (#574).

apt's ``DPkg::Post-Invoke`` hook (``/etc/apt/apt.conf.d/99needrestart``) runs
``needrestart -m u`` after every dpkg run. Ubuntu's patch turns ``-m u`` into
**automatic** restarts when ``$nrconf{restart}`` is unset — the stock state here,
``/etc/needrestart/conf.d/`` holding only its README — so a ``python3.12`` or
``libssl3t64`` security update would restart ``power-map.service`` mid-apply, and
that restart runs ``apply-schema.sh`` against the managed DB, outside the owner's
approval. ``NEEDRESTART_MODE=l`` in the environment overrides that only if it
survives every process between the operator and the hook; the drop-in makes the
answer not depend on it. Same shape as CannObserv/broker ``ad03a3d``,
CannObserv/archiver ``4409060`` and CannObserv/usa-wa#432.

These tests read the tracked artifact, not the live host: the gate must hold in a
worktree and in CI. The install-time check is in ``docs/COMMANDS.md``.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

DROP_IN = Path(__file__).resolve().parents[1] / "infra" / "needrestart.conf.d" / "power-map.conf"

# needrestart's config is Perl, eval'd into ``%nrconf``. Evaluating it the same way
# is the only honest parse: a syntax error makes needrestart die, and the apt hook
# swallows that with ``|| true``.
_EVAL = (
    "our %nrconf; our $LOGPREF = q(); "
    "eval do { local(@ARGV, $/) = $ARGV[0]; <> }; die $@ if $@; "
    "print defined $nrconf{restart} ? $nrconf{restart} : q(undef);"
)


def _restart_mode() -> str:
    """Return ``$nrconf{restart}`` after evaluating the drop-in as needrestart does."""
    perl = shutil.which("perl")
    if perl is None:
        pytest.skip("perl not available on this host")
    return subprocess.run(
        [perl, "-e", _EVAL, str(DROP_IN)], capture_output=True, text=True, check=True
    ).stdout


def test_the_drop_in_sets_list_only_restart_mode():
    """``l`` lists what needs a restart and restarts nothing."""
    assert _restart_mode() == "l"


def test_the_drop_in_sets_nothing_else():
    """One key, so the drop-in cannot quietly change needrestart's other behaviour.

    ``$nrconf{ui}`` in particular stays unset: setting it without this key forces
    interactive mode instead.
    """
    lines = [
        ln.strip()
        for ln in DROP_IN.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert lines == ["$nrconf{restart} = 'l';"]
