"""The two root-disk units run their scripts the way the scripts require (#665).

Both scripts are standard library only and must run when the disk is too full
for ``uv run`` to write what it needs, so each unit calls ``/usr/bin/python3``
on the file in the deployed checkout, never ``uv run``. The prune unit is the
one that writes, so it alone passes ``--execute``; the guard's thresholds are
the ones #665 and #690 agreed, and its hysteresis state lives in the unit's own
``StateDirectory=``. Both keep systemd's ``SyslogLevelPrefix=`` on, which is
what turns the scripts' ``<N>`` prefixes into journal priorities (#690), and
the prune runs daily so it keeps pace with VS Code updates (#690). Generic
check-in wiring is ``test_infra_alerting.py``'s.
"""

from pathlib import Path

from scripts.check_disk_headroom import DEFAULT_ALERT_AT, DEFAULT_CLEAR_AT
from tests.test_infra_alerting import environment, values

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
HEADROOM = INFRA / "power-map-disk-headroom.service"
PRUNE = INFRA / "power-map-disk-prune.service"
PRUNE_TIMER = INFRA / "power-map-disk-prune.timer"
DEPLOYED = Path("/home/exedev/power-map")


def _exec_start(unit: Path) -> list[str]:
    [line] = values(unit, "Service", "ExecStart")
    return line.split()


def test_both_run_system_python_on_a_real_script():
    for unit in (HEADROOM, PRUNE):
        argv = _exec_start(unit)
        assert argv[0] == "/usr/bin/python3", unit.name
        script = Path(argv[1])
        assert script.is_relative_to(DEPLOYED / "scripts"), unit.name
        assert (REPO / script.relative_to(DEPLOYED)).is_file(), unit.name
        assert "uv" not in argv, unit.name


def test_the_guard_alerts_at_the_agreed_threshold():
    argv = _exec_start(HEADROOM)
    assert argv[argv.index("--alert-at") + 1] == str(DEFAULT_ALERT_AT) == "85"
    assert "--execute" not in argv


def test_the_guard_clears_at_the_agreed_threshold():
    argv = _exec_start(HEADROOM)
    assert argv[argv.index("--clear-at") + 1] == str(DEFAULT_CLEAR_AT) == "80"


def test_the_guard_keeps_its_state_in_its_own_state_directory():
    """systemd creates /var/lib/power-map-disk-headroom, owned by User=, and
    hands the script its path as $STATE_DIRECTORY."""
    assert values(HEADROOM, "Service", "StateDirectory") == ["power-map-disk-headroom"]


def test_both_keep_the_syslog_level_prefix_on():
    for unit in (HEADROOM, PRUNE):
        assert values(unit, "Service", "SyslogLevelPrefix") == ["yes"], unit.name


def test_the_prune_runs_daily():
    """VS Code shipped two server builds in one week (#690); weekly fell behind."""
    [calendar] = values(PRUNE_TIMER, "Timer", "OnCalendar")
    assert calendar == "*-*-* 05:30:00 UTC"
    assert values(PRUNE_TIMER, "Timer", "Persistent") == ["true"]


def test_the_guard_names_its_alert_exit():
    assert environment(HEADROOM)["STATUS_EXIT_KINDS"] == "3:low-headroom"


def test_only_the_prune_writes():
    assert _exec_start(PRUNE)[2:] == ["--execute"]


def test_both_run_as_the_session_user():
    """The prune's targets live in ~exedev, and /proc is read as that user."""
    for unit in (HEADROOM, PRUNE):
        assert values(unit, "Service", "User") == ["exedev"], unit.name
