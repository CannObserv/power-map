"""The two root-disk units run their scripts the way the scripts require (#665).

Both scripts are standard library only and must run when the disk is too full
for ``uv run`` to write what it needs, so each unit calls ``/usr/bin/python3``
on the file in the deployed checkout, never ``uv run``. The prune unit is the
one that writes, so it alone passes ``--execute``; the guard's threshold is the
one #665 agreed. Generic check-in wiring is ``test_infra_alerting.py``'s.
"""

from pathlib import Path

from scripts.check_disk_headroom import DEFAULT_ALERT_AT
from tests.test_infra_alerting import environment, values

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
HEADROOM = INFRA / "power-map-disk-headroom.service"
PRUNE = INFRA / "power-map-disk-prune.service"
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


def test_the_guard_names_its_alert_exit():
    assert environment(HEADROOM)["STATUS_EXIT_KINDS"] == "3:low-headroom"


def test_only_the_prune_writes():
    assert _exec_start(PRUNE)[2:] == ["--execute"]


def test_both_run_as_the_session_user():
    """The prune's targets live in ~exedev, and /proc is read as that user."""
    for unit in (HEADROOM, PRUNE):
        assert values(unit, "Service", "User") == ["exedev"], unit.name
