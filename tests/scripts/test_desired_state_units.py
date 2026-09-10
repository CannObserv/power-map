"""The nightly desired-state chain's systemd units (#499 step 9).

One oneshot service runs the three PM-side steps in order — export, build,
apply (dry) — each under `uv run --group mapping`, because the API service's
own `uv sync` prunes that group from the main checkout's venv. systemd runs
multiple ExecStart= lines of a oneshot sequentially and stops at the first
failure, so a broken step reaches `systemctl --failed`. The timer fires after
the 09:00 UTC pull.
"""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra"
SERVICE = INFRA / "power-map-desired-state.service"
TIMER = INFRA / "power-map-desired-state.timer"
PULL_TIMER = INFRA / "power-map-datasets-pull.timer"


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
