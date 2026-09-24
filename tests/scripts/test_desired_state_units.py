"""The nightly desired-state chain's systemd units (#499 step 9).

One oneshot service runs the three PM-side steps in order — export, build,
apply (dry) — each under `uv run --group mapping`, because the API service's
own `uv sync` prunes that group from the main checkout's venv. systemd runs
multiple ExecStart= lines of a oneshot sequentially and stops at the first
failure, so a broken step reaches `systemctl --failed`. The timer fires after
the 09:00 UTC pull.

The pull is a separate unit on its own timer, and its failure does not hold the
chain back — by design (#535): a chain that ran on known-stale inputs and says
so is worth more than no nightly diff. What connects the two is data, not a
dependency: `pull.json`'s age and offer, which the build judges.
"""

import re
from datetime import timedelta
from pathlib import Path

from src.core.ingestion.datasets import PULL_MAX_AGE

INFRA = Path(__file__).resolve().parents[2] / "infra"
SERVICE = INFRA / "power-map-desired-state.service"
TIMER = INFRA / "power-map-desired-state.timer"
PULL_TIMER = INFRA / "power-map-datasets-pull.timer"
PULL_UNIT = "power-map-datasets-pull.service"


def _lines(path: Path, key: str) -> list[str]:
    return [ln.split("=", 1)[1] for ln in path.read_text().splitlines() if ln.startswith(f"{key}=")]


def test_the_service_runs_the_three_steps_in_order_under_the_mapping_group():
    steps = _lines(SERVICE, "ExecStart")

    assert [s.split("scripts.")[1].split()[0] for s in steps] == [
        "export_pm_tables",
        "build_desired_state",
        "apply_desired_state",
    ]
    assert all(s.startswith("uv run --group mapping python -m scripts.") for s in steps)


def test_the_service_never_executes():
    """The timer's applier run is the dry run the streak counts; --execute is a person's."""
    assert not any("--execute" in s for s in _lines(SERVICE, "ExecStart"))


def test_the_service_is_a_oneshot_in_the_main_checkout_with_both_env_files():
    text = SERVICE.read_text()

    assert _lines(SERVICE, "Type") == ["oneshot"]
    assert _lines(SERVICE, "WorkingDirectory") == ["/home/exedev/power-map"]
    assert "EnvironmentFile=/etc/power-map/.env" in text
    assert "EnvironmentFile=-/home/exedev/power-map/.env" in text


def test_the_timer_fires_after_the_pull_and_catches_up_a_missed_night():
    pull = _lines(PULL_TIMER, "OnCalendar")[0]
    chain = _lines(TIMER, "OnCalendar")[0]
    hhmm = lambda cal: re.search(r"(\d\d:\d\d):\d\d", cal).group(1)  # noqa: E731

    assert hhmm(chain) > hhmm(pull)
    assert "UTC" in chain
    assert _lines(TIMER, "Persistent") == ["true"]
    assert "WantedBy=timers.target" in TIMER.read_text()


def _at(timer: Path) -> timedelta:
    """A daily timer's firing moment as an offset from midnight."""
    hh, mm = re.search(r"(\d\d):(\d\d):\d\d", _lines(timer, "OnCalendar")[0]).groups()
    return timedelta(hours=int(hh), minutes=int(mm))


def _jitter(timer: Path) -> timedelta:
    return timedelta(seconds=int(_lines(timer, "RandomizedDelaySec")[0]))


def test_the_chain_is_ordered_after_the_pull_but_does_not_require_it():
    """#535: `After=` holds the chain's start while the pull has a start job of
    its own — still running at 09:30 on a slow night (a oneshot is started only
    once its process exits), or fired together with it by a `Persistent=`
    catch-up — so the build waits for the pull rather than racing it. It never
    holds the chain back on a failed pull: `pull.json`'s age does that job.
    `Requires=` or `Wants=` would start a second pull, and the first would also
    stop the nightly diff whenever the pull fails."""
    after = " ".join(_lines(SERVICE, "After")).split()

    assert PULL_UNIT in after
    assert not any(
        PULL_UNIT in v for key in ("Requires", "Wants", "BindsTo") for v in _lines(SERVICE, key)
    )


def test_the_pull_age_bound_tells_a_missed_pull_from_a_taken_one():
    """`PULL_MAX_AGE` is judged at build time against the last `pulled_at`. The
    nightly build must read tonight's pull as fresh even at the latest both
    timers can fire, and yesterday's as overdue even at the earliest — or a
    failed catalog fetch leaves the chain reading an offer a day old as current."""
    pull, chain = _at(PULL_TIMER), _at(TIMER)

    latest_fresh = (chain + _jitter(TIMER)) - pull
    earliest_missed = (timedelta(days=1) + chain) - (pull + _jitter(PULL_TIMER))

    assert latest_fresh < PULL_MAX_AGE < earliest_missed
