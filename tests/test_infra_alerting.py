"""Every scheduled timer checks in with co-status; the API dispatches its own failure (#566).

Two push paths replace ``systemctl --failed`` as the only failure signal:

- **Each ``power-map-*.timer`` service** runs ``scripts/status_checkin.py`` as
  ``ExecStopPost=`` against its own co-status monitor (CannObserv/status#36).
  A unit without it is a timer whose failures, and whose silence, reach no one.
- **``power-map.service``** carries ``OnFailure=`` to a Notifier dispatch
  handler (CannObserv/notifier#114), with ``RestartMode=direct`` and a start
  limit so that a crash loop ends in ``failed`` exactly once.

These read the tracked units, not the live host: the #560 drift guard holds the
installed copies to them.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.status_checkin import parse_exit_kinds

INFRA = Path(__file__).resolve().parents[1] / "infra"
TIMERS = sorted(INFRA.glob("power-map-*.timer"))
SERVICES = [t.with_suffix(".service") for t in TIMERS]
API = INFRA / "power-map.service"
HANDLER = INFRA / "power-map-notify-failure@.service"

HOOK = "-/usr/bin/python3 /home/exedev/power-map/scripts/status_checkin.py %n"
STATUS_KEY = "status-checkin:/etc/power-map/status-api.key"
NOTIFIER_KEY = "notifier-api:/etc/power-map/notifier-api.key"
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# The monitor ids came from status#36 (comment 6128848036, 2026-10-12); each
# unit's comment names its monitor. Pending, every unit carried an empty id and
# the hook skipped; this stays as the switch a re-provisioning would flip back.
MONITOR_IDS_PENDING = False
# Units whose monitor is requested but not yet provisioned (#665's two disk
# timers): each carries an empty id, so the hook skips, logged. Remove a name
# once its id lands in the unit.
PENDING_MONITORS = frozenset({"power-map-disk-headroom", "power-map-disk-prune"})


def directives(path: Path) -> dict[str, list[tuple[str, str]]]:
    """``{section: [(key, value), ...]}`` in file order; repeated keys kept."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = ""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        key, _, value = line.partition("=")
        sections.setdefault(current, []).append((key.strip(), value.strip()))
    return sections


def values(path: Path, section: str, key: str) -> list[str]:
    return [v for k, v in directives(path).get(section, []) if k == key]


def environment(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for value in values(path, "Service", "Environment"):
        name, _, val = value.strip('"').partition("=")
        env[name] = val
    return env


def test_there_are_fifteen_timers_each_with_a_service():
    """status#36 holds one monitor per timer; a new timer needs a new monitor."""
    assert len(TIMERS) == 15
    assert all(s.exists() for s in SERVICES)


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_every_timer_service_checks_in_after_every_run(service):
    assert values(service, "Service", "ExecStopPost") == [HOOK]


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_the_key_is_a_credential_with_an_empty_fallback(service):
    """``SetCredential=`` keeps a missing key file from failing the unit (243).

    Its value must be non-empty (systemd ignores ``status-checkin:`` as invalid
    syntax, leaving no fallback), so a lone newline, which the hook strips to
    "no key" (co-status's ``hc-ping-key`` precedent).
    """
    assert values(service, "Service", "LoadCredential") == [STATUS_KEY]
    assert values(service, "Service", "SetCredential") == ["status-checkin:\\n"]
    assert not any("STATUS" in k and "KEY" in k for k in environment(service))


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_every_timer_service_names_its_monitor(service):
    """The comment names the status#36 monitor, so an id pasted into the wrong unit shows."""
    assert "STATUS_MONITOR_ID" in environment(service)
    assert f"# co-status monitor: co-{service.stem}" in service.read_text()


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_exit_kinds_parse_cleanly(service):
    raw = environment(service).get("STATUS_EXIT_KINDS")
    if raw is None:
        return
    pairs = [p for p in raw.split(",") if p.strip()]
    assert len(parse_exit_kinds(raw)) == len(pairs), raw


def test_monitor_ids_are_distinct_ulids_or_all_pending():
    ids = {s.stem: environment(s)["STATUS_MONITOR_ID"] for s in SERVICES}
    if MONITOR_IDS_PENDING:
        assert list(ids.values()) == [""] * len(SERVICES)
        return
    pending = {stem: i for stem, i in ids.items() if stem in PENDING_MONITORS}
    provisioned = [i for stem, i in ids.items() if stem not in PENDING_MONITORS]
    assert all(i == "" for i in pending.values()), pending
    assert all(ULID.match(i) for i in provisioned), provisioned
    assert len(set(provisioned)) == len(provisioned)


def test_every_pending_monitor_names_a_real_timer():
    """A stale name here would let a provisioned unit keep an empty id unnoticed."""
    assert PENDING_MONITORS <= {s.stem for s in SERVICES}


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_no_unit_still_promises_onfailure_later(service):
    assert "OnFailure= later" not in service.read_text()
    assert "drive OnFailure=" not in service.read_text()


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.stem)
def test_timer_services_do_not_open_github_issues_for_alerts_except_a11y(service):
    """#566 retired the GitHub-issue alert path; a11y's issue is a work item, kept."""
    if service.stem == "power-map-a11y":
        return
    text = service.read_text()
    assert "GitHub issue" not in text
    assert "gh (/bin/gh)" not in text


# --- power-map.service: OnFailure= through Notifier --------------------------


def test_the_api_dispatches_its_own_failure():
    assert values(API, "Unit", "OnFailure") == ["power-map-notify-failure@%n.service"]


def test_a_crash_loop_ends_in_failed_exactly_once():
    """``RestartMode=direct`` keeps each restart out of ``failed``; the limit ends the loop."""
    assert values(API, "Service", "Restart") == ["on-failure"]
    assert values(API, "Service", "RestartMode") == ["direct"]
    [interval] = values(API, "Unit", "StartLimitIntervalSec")
    [burst] = values(API, "Unit", "StartLimitBurst")
    [restart_sec] = values(API, "Service", "RestartSec")
    # The loop must be able to reach the burst inside the interval.
    assert int(burst) * int(restart_sec.rstrip("s")) < int(interval)


def test_the_handler_logs_first_then_dispatches_fail_open():
    starts = values(HANDLER, "Service", "ExecStart")
    assert starts[0].startswith("/usr/bin/logger ")
    assert "%i" in starts[0]
    assert starts[1].startswith("-/home/exedev/power-map/.venv/bin/python ")
    assert starts[1].endswith("-m scripts.notify_unit_failure %i")
    assert values(HANDLER, "Service", "Type") == ["oneshot"]
    assert values(HANDLER, "Service", "WorkingDirectory") == ["/home/exedev/power-map"]


def test_the_handler_reads_its_key_as_a_credential():
    assert values(HANDLER, "Service", "LoadCredential") == [NOTIFIER_KEY]
    assert values(HANDLER, "Service", "SetCredential") == ["notifier-api:\\n"]


def test_the_handler_has_no_onfailure_of_its_own():
    """A failing handler would recurse."""
    assert values(HANDLER, "Unit", "OnFailure") == []


def test_the_handler_names_its_template_and_channels():
    env = environment(HANDLER)
    """Provisioned on notifier#114 (2026-10-12): the template, plus Slack and Mailgun."""
    assert env["NOTIFIER_URL"] == "http://notifier:9000"
    assert ULID.match(env["NOTIFIER_TEMPLATE_ID"])
    channels = env["NOTIFIER_CHANNEL_IDS"].split(",")
    assert len(channels) == 2 and all(ULID.match(c) for c in channels)


def test_the_handler_is_the_only_onfailure_target():
    targets = {v for unit in INFRA.glob("*.service") for v in values(unit, "Unit", "OnFailure")}
    assert targets == {"power-map-notify-failure@%n.service"}


# --- systemd itself parses them ---------------------------------------------

# Lines systemd-analyze prints for a unit it misreads. Host-specific findings (a
# missing executable on a CI runner) are deliberately not in this list.
PARSE_ERRORS = ("Invalid syntax", "Unknown key", "Unknown section", "Failed to parse")


def test_systemd_parses_every_touched_unit_cleanly():
    """The empty ``SetCredential=status-checkin:`` was ignored as invalid, silently."""
    analyze = shutil.which("systemd-analyze")
    if analyze is None:
        pytest.skip("systemd-analyze not available")
    units = [*SERVICES, API, HANDLER]
    proc = subprocess.run(
        [analyze, "verify", *map(str, units)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    errors = [ln for ln in proc.stderr.splitlines() if any(e in ln for e in PARSE_ERRORS)]
    assert errors == []
