"""Every timer has a declared retry policy, and every retried unit fits its budget (#833).

On 2026-11-17 a ten-minute network outage cost each daily unit a day. The policy
(``docs/ALERTING.md`` § Transient failures) puts each timer in one of four places:

- **next tick**: the cadence is the retry (every 2 or 5 minutes);
- **no network**: nothing to retry but the check-in;
- **accepted skip**: openapi-parity's tag check (#824's limit, kept by #833 Q5);
- **in-process**: the run retries its connect or GET through ``src.core.retry``
  on one shared budget, and reports one verdict.

A new timer without a row here fails ``test_every_timer_has_a_retry_policy``.
For each in-process unit, the worst case (every attempt timing out, the whole
budget slept) must fit the unit's ``TimeoutStartSec`` and the co-status grace
(CannObserv/status#36), so a retrying run never shows ``missing``.
"""

import ast
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from scripts import status_checkin
from scripts.classify_suggestions import BUSY_RETRIES, BUSY_WAIT, JEV_ATTEMPTS, JEV_TIMEOUT
from scripts.pull_datasets import REQUEST_TIMEOUT
from src.core.retry import CONNECT_TIMEOUT, DELAYS
from tests.test_infra_alerting import INFRA, SERVICES, TIMERS, values

ROOT = INFRA.parent

NEXT_TICK = "next-tick"
NO_NETWORK = "no-network"
ACCEPTED_SKIP = "accepted-skip"

# co-status grace_seconds, CannObserv/status#36.
DAILY_GRACE = 7200
HOURLY_GRACE = 1800
# uv start-up, imports, logging: what a run costs before its first network call.
HEADROOM = 120
# systemd's default TimeoutStopSec, which bounds the ExecStopPost= check-in.
DEFAULT_TIMEOUT_STOP = 90


@dataclass(frozen=True)
class InProcess:
    """A unit whose scripts retry their network call on one budget per process."""

    modules: tuple[str, ...]  # the ExecStart modules that touch the network
    attempt_timeout: float  # the most one failed attempt can take
    normal: float  # a generous ordinary run, all its processes together
    grace: int = DAILY_GRACE
    calls: int = 1  # retried calls in one process (schema-parity connects twice)

    def worst_case(self) -> float:
        """Every attempt times out and the whole budget is slept, in every process."""
        failed = len(DELAYS) + self.calls
        per_process = sum(DELAYS) + failed * self.attempt_timeout
        return len(self.modules) * per_process + self.normal + HEADROOM


def _db(module: str, *, grace: int = DAILY_GRACE, calls: int = 1) -> InProcess:
    return InProcess((module,), CONNECT_TIMEOUT, normal=10, grace=grace, calls=calls)


RETRY_POLICY: dict[str, str | InProcess] = {
    "power-map-ready": NEXT_TICK,
    "power-map-egress-ip": NEXT_TICK,
    "power-map-infra-drift": NO_NETWORK,
    "power-map-openapi-parity": ACCEPTED_SKIP,
    "power-map-anomaly": _db("scripts.check_api_anomalies", grace=HOURLY_GRACE),
    "power-map-ancillary-orphans": _db("scripts.audit_ancillary_orphans"),
    "power-map-assignment-rel-windows": _db("scripts.audit_assignment_relationship_windows"),
    "power-map-prune": _db("scripts.prune"),
    "power-map-seat-occupancy": _db("scripts.audit_seat_occupancy"),
    "power-map-schema-parity": _db("scripts.audit_schema_constraint_parity", calls=2),
    "power-map-datasets-pull": InProcess(("scripts.pull_datasets",), REQUEST_TIMEOUT, normal=30),
    # #877: the DB connect and every Jev request share one budget. A Jev attempt
    # is bounded by the script (JEV_TIMEOUT x JEV_ATTEMPTS + its 429 sleeps);
    # normal is 200 orgs, 4 in flight, a few seconds each, plus the wait for a
    # drain already running (BUSY_RETRIES x BUSY_WAIT).
    "power-map-classification": InProcess(
        ("scripts.classify_suggestions",),
        JEV_TIMEOUT * JEV_ATTEMPTS + sum(2**i for i in range(JEV_ATTEMPTS - 1)),
        normal=300 + BUSY_RETRIES * BUSY_WAIT,
    ),
    "power-map-desired-state": InProcess(
        ("scripts.export_pm_tables", "scripts.apply_desired_state"), CONNECT_TIMEOUT, normal=60
    ),
}

IN_PROCESS = sorted(k for k, v in RETRY_POLICY.items() if isinstance(v, InProcess))
DB_MODULES = sorted(
    m
    for v in RETRY_POLICY.values()
    if isinstance(v, InProcess) and v.attempt_timeout == CONNECT_TIMEOUT
    for m in v.modules
)


def _service(stem: str) -> Path:
    return INFRA / f"{stem}.service"


def _timer(stem: str) -> Path:
    return INFRA / f"{stem}.timer"


def _seconds(stem: str, key: str) -> int | None:
    found = values(_service(stem), "Service", key)
    return int(found[-1]) if found else None


def _module_tree(module: str) -> ast.Module:
    return ast.parse((ROOT / (module.replace(".", "/") + ".py")).read_text())


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _is_attr_call(call: ast.Call, owner: str, attr: str) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == attr
        and isinstance(func.value, ast.Name)
        and func.value.id == owner
    )


def _names_called(tree: ast.Module) -> set[str]:
    return {c.func.id for c in _calls(tree) if isinstance(c.func, ast.Name)}


# --- the roster -------------------------------------------------------------


def test_every_timer_has_a_retry_policy():
    """A new timer must say how it rides out a blip before it ships."""
    assert {t.stem for t in TIMERS} == set(RETRY_POLICY)


def test_every_in_process_module_exists():
    for stem in IN_PROCESS:
        for module in RETRY_POLICY[stem].modules:
            assert (ROOT / (module.replace(".", "/") + ".py")).is_file(), module


@pytest.mark.parametrize("stem", IN_PROCESS)
def test_in_process_modules_are_what_the_unit_runs(stem):
    """The table must not drift from the unit: a renamed script would escape the sweep."""
    starts = " ".join(values(_service(stem), "Service", "ExecStart"))
    for module in RETRY_POLICY[stem].modules:
        assert f"-m {module}" in starts, (stem, module)


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_no_timer_service_restarts_itself(service):
    """#833 Q1: ExecStopPost= would check in after every attempt, alert then ok."""
    assert values(service, "Service", "Restart") == []


# --- next tick --------------------------------------------------------------


@pytest.mark.parametrize("stem", sorted(k for k, v in RETRY_POLICY.items() if v == NEXT_TICK))
def test_next_tick_units_fire_at_least_every_five_minutes(stem):
    """Their retry *is* the next tick; a slower cadence would need a policy of its own."""
    calendar = values(_timer(stem), "Timer", "OnCalendar")
    assert len(calendar) == 1
    match = re.fullmatch(r"\*:0/(\d+)", calendar[0])
    assert match and int(match.group(1)) <= 5, calendar


# --- in-process budgets -----------------------------------------------------


@pytest.mark.parametrize("stem", IN_PROCESS)
def test_in_process_units_bound_their_start(stem):
    """oneshot has no start timeout by default: a hung connect would hold the unit forever."""
    assert _seconds(stem, "TimeoutStartSec") is not None, f"{stem} sets no TimeoutStartSec"


@pytest.mark.parametrize("stem", IN_PROCESS)
def test_worst_case_run_fits_the_units_start_timeout(stem):
    """A unit killed mid-retry reports `timeout`, not its own verdict."""
    policy = RETRY_POLICY[stem]
    limit = _seconds(stem, "TimeoutStartSec")
    assert policy.worst_case() <= limit, (stem, policy.worst_case(), limit)


@pytest.mark.parametrize("stem", IN_PROCESS)
def test_a_retrying_run_never_reads_missing(stem):
    """The longest possible run plus its check-in must land inside the monitor's grace."""
    policy = RETRY_POLICY[stem]
    assert _seconds(stem, "TimeoutStartSec") + DEFAULT_TIMEOUT_STOP <= policy.grace


def _daily_at(stem: str) -> timedelta:
    (calendar,) = values(_timer(stem), "Timer", "OnCalendar")
    match = re.fullmatch(r"\*-\*-\* (\d\d):(\d\d):(\d\d) UTC", calendar)
    assert match, calendar
    hours, minutes, seconds = map(int, match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _jitter(stem: str) -> timedelta:
    found = values(_timer(stem), "Timer", "RandomizedDelaySec")
    return timedelta(seconds=int(found[0]) if found else 0)


def test_the_pull_is_done_before_desired_state_can_start():
    """The chain: desired-state reads what the pull lands, so the pull's window ends first."""
    latest_pull = _daily_at("power-map-datasets-pull") + _jitter("power-map-datasets-pull")
    earliest_desired = _daily_at("power-map-desired-state")
    gap = (earliest_desired - latest_pull).total_seconds()
    limit = _seconds("power-map-datasets-pull", "TimeoutStartSec")
    assert limit + DEFAULT_TIMEOUT_STOP <= gap, (limit, gap)


# --- the call sites go through the helper -----------------------------------


@pytest.mark.parametrize("module", DB_MODULES)
def test_db_timer_scripts_connect_through_the_retry_helper(module):
    """A bare asyncpg.connect fails on its first error, as all of them did on 11-17."""
    tree = _module_tree(module)
    assert not [c for c in _calls(tree) if _is_attr_call(c, "asyncpg", "connect")], module
    assert "connect_with_retry" in _names_called(tree), module


def test_the_pull_retries_every_publisher_request():
    """`_get` is the one door for the catalog and every file."""
    tree = _module_tree("src.core.ingestion.datasets")
    (get,) = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_get"]
    assert "retry_async" in {
        c.func.id for c in ast.walk(get) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert "Backoff" in _names_called(_module_tree("scripts.pull_datasets"))


# --- the check-in hook ------------------------------------------------------


def test_the_check_in_fits_its_stop_timeout():
    """ExecStopPost= runs under TimeoutStopSec; a killed hook loses the verdict."""
    worst = (
        status_checkin.ATTEMPTS * status_checkin.TIMEOUT_SECONDS
        + (status_checkin.ATTEMPTS - 1) * status_checkin.RETRY_DELAY_SECONDS
    )
    assert worst + 20 <= DEFAULT_TIMEOUT_STOP


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_no_timer_shortens_the_check_ins_stop_timeout(service):
    found = values(service, "Service", "TimeoutStopSec")
    assert all(int(v) >= DEFAULT_TIMEOUT_STOP for v in found)
