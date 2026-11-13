"""The patching-hosts knob reads clean, and keeps the decisions it records (#658).

``.skills/patching-hosts`` is what the vendored ``patching-hosts`` skill reads to
learn what only this repo's owner knows: the window, the health check, what
the reboot chain stops. **A malformed line makes the host report-only** until it
is fixed, which reads as "nothing to do" at the next run rather than as an
error, so the skill's own reader is run here, through the ``skills/`` link the
skill is discovered by.

The rest are pure parses of the file:

- **The posture is scheduled, weekly, in the owner's Wednesday window**
  (owner, 2026-11-13). ``cadence`` absent also reads weekly, so the reader's
  ``line`` is what tells a stated cadence from a default.
- **A dated line is allowed to have passed.** A dated window turns into a
  ``past-window`` finding the day after, and an exception into an
  ``expired-exception`` one at its review-by date. Neither makes the host
  report-only, and the probe reports each at every run until the line is
  changed. A prune's review-by date is its soak, so its expiry is the cue
  for the next stage, not a fault. Every other finding reds.
- **The one health line checks ``/ready``**, not ``/health``: the managed DB is
  gated on the egress IP, which can rotate across a reboot (#410), and only
  ``/ready`` reaches the DB.
- **No knob command escalates.** ``health``, ``inflight`` and ``recovery`` run as
  the invoking user by design; a ``sudo`` in one would hand root to whoever
  commits the knob.
- **No data store, no image owner.** The database is managed Postgres, so
  there's nothing local to dump. ``image-owner`` would publish a private
  repo's name from this public one (gregoryfoster/skills#350).
- **Docker, nginx and fwupd are pruned, not kept** (owner, 2026-11-13): each
  stage that ran is declared, and none is ``keep:``. fwupd is also held, so
  its conffile prompt (the exeuntu image deletes the file) can't stop the
  maintenance lane while the prune soaks.
- **Every ``uv run`` timer service is declared not a restarter.** The probe's
  restarter scan can't follow a ``uv run`` module, so each is a finding until
  the knob says it only audits or reports. Adding a timer without its line reds.
- **Each third-party apt origin has a policy**: Tailscale, NodeSource and the
  GitHub CLI are followed in the maintenance lane, by site.
- **No step overlaps a scheduled job.** Each timer in ``infra/`` that fires at a
  fixed time of day lands inside a ``quiet`` range, randomised delay and a
  minute of run time included, so moving a timer without the knob reds.
"""

import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from tests import vendor_skills

# Reads the vendored tree: runs on a skills-vendor/ bump's commit too (#751).
pytestmark = pytest.mark.vendored

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOB = REPO_ROOT / ".skills" / "patching-hosts"
READ_KNOB = REPO_ROOT / "skills" / "patching-hosts" / "scripts" / "read-knob.sh"
INFRA = REPO_ROOT / "infra"

# The host the knob is read for; it has no [host] sections, so any name reads
# the same lines.
HOST = "power-map"

# Time a fixed-time job needs after its randomised start. Measured runs take
# at most 6 s (desired-state's dbt build); the minute is headroom.
RUN_TIME = timedelta(minutes=1)

# Findings a dated line raises once its date passes: reminders, not faults.
DATED_KINDS = {"past-window", "expired-exception"}

# The owner's prunes (2026-11-13): the stage each has reached.
PRUNED = {"docker": "disabled", "nginx": "disabled", "fwupd": "disabled"}

# Third-party apt origins on this host, each by its site.
ORIGINS = {"pkgs.tailscale.com", "deb.nodesource.com", "cli.github.com"}

vendor_only = pytest.mark.skipif(
    not vendor_skills.vendor_skills_present(),
    reason=vendor_skills.SKIP_REASON,
)


def _directives() -> list[list[str]]:
    """Each directive line's words. ``#`` opens a comment at a line's start or
    after whitespace (the skill's knob.md), so a URL keeps a bare ``#``."""
    lines = []
    for raw in KNOB.read_text(encoding="utf-8").splitlines():
        words = []
        for word in raw.split():
            if word.startswith("#"):
                break
            words.append(word)
        if words:
            lines.append(words)
    return lines


def _named(directive: str) -> list[list[str]]:
    """The arguments of every line declaring ``directive``."""
    return [words[1:] for words in _directives() if words[0] == directive]


def _read_knob() -> dict:
    """The vendored reader's resolution of this knob for ``HOST``."""
    out = subprocess.run(
        ["bash", str(READ_KNOB), "--config", str(KNOB), "--host", HOST],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def _clock(hhmm: str) -> timedelta:
    hours, minutes = hhmm.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))


def _fixed_time_timers() -> dict[str, tuple[timedelta, timedelta]]:
    """Each tracked timer firing at a fixed time of day: (start, latest end).

    ``daily`` is 00:00; ``*-*-* HH:MM:SS UTC`` is its time. Sub-daily timers
    (the 2-min ``/ready`` and 5-min egress probes, the hourly anomaly check)
    are read-only probes no window could avoid, and are skipped.
    """
    timers = {}
    for path in sorted(INFRA.glob("*.timer")):
        text = path.read_text(encoding="utf-8")
        calendar = re.search(r"^OnCalendar=(.+)$", text, re.MULTILINE)
        if calendar is None:
            continue
        value = calendar.group(1).strip()
        if value == "daily":
            start = timedelta(0)
        else:
            fixed = re.fullmatch(r"\*-\*-\* (\d\d:\d\d):\d\d(?: UTC)?", value)
            if fixed is None:
                continue
            start = _clock(fixed.group(1))
        delay = re.search(r"^RandomizedDelaySec=(\d+)$", text, re.MULTILINE)
        spread = timedelta(seconds=int(delay.group(1))) if delay else timedelta(0)
        timers[path.name] = (start, start + spread + RUN_TIME)
    return timers


def test_the_knob_exists() -> None:
    assert KNOB.is_file(), f"missing {KNOB.relative_to(REPO_ROOT)}"


@vendor_only
def test_the_knob_reads_clean() -> None:
    """The skill's own reader: no finding but a passed dated window, not report-only."""
    assert READ_KNOB.is_file(), "skills/patching-hosts is not linked to the vendored skill"
    knob = _read_knob()
    findings = [f for f in knob["findings"] if f["kind"] not in DATED_KINDS]
    assert findings == []
    assert knob["report_only"] is False, knob["report_only_reasons"]
    assert knob["class"]["value"] == "production"
    assert knob["posture"]["value"] == "scheduled"
    assert knob["cadence"]["value"] == "weekly"
    assert knob["cadence"]["line"] is not None, "weekly by default, not stated"
    weekly = [w for w in knob["window"] if w["date"] is None]
    assert [(w["weekday"], w["week"], w["start"], w["end"]) for w in weekly] == [
        ("Wed", None, "01:00", "07:00")
    ]
    assert knob["datastore"] == []
    assert knob["image_owner"]["value"] is None


def test_the_cadence_is_stated_weekly() -> None:
    assert _named("cadence") == [["weekly"]]


def test_the_weekly_window_is_wednesday_morning_utc() -> None:
    weekly = [w for w in _named("window") if not re.fullmatch(r"\d{4}-\d\d-\d\d", w[0])]
    assert weekly == [["Wed", "01:00-07:00"]]


def test_the_one_health_line_checks_ready() -> None:
    health = [" ".join(words) for words in _named("health")]
    assert len(health) == 1, health
    command = health[0]
    assert "http://127.0.0.1:8000/ready" in command
    assert command.startswith("curl -sf "), "a non-2xx must fail the check"
    assert "--max-time" in command, "a hung DB must not use up the 60 s knob limit"


def test_power_map_is_the_service_the_chain_stops() -> None:
    assert _named("service") == [["power-map.service"]]


def test_no_knob_command_escalates() -> None:
    commands = [
        " ".join(words) for words in _named("health") + _named("inflight") + _named("recovery")
    ]
    assert commands, "the knob declares no health line"
    for command in commands:
        for word in ("sudo", "runuser", "su ", "pkexec", "systemd-creds"):
            assert word not in command, f"{word!r} in a knob command: {command}"


def test_no_datastore_and_no_image_owner() -> None:
    assert _named("datastore") == []
    assert _named("backup") == []
    assert _named("image-owner") == []


def test_docker_is_not_kept() -> None:
    kept = [words for words in _named("exception") if words and words[0] == "keep:docker"]
    assert kept == []


def test_every_fixed_time_job_is_inside_a_quiet_range() -> None:
    ranges = []
    for words in _named("quiet"):
        start, end = words[0].split("-")
        ranges.append((_clock(start), _clock(end)))
    timers = _fixed_time_timers()
    assert timers, "no fixed-time timer found in infra/: the parse is broken"
    for name, (start, latest) in timers.items():
        assert any(lo <= start and latest <= hi for lo, hi in ranges), (
            f"{name} runs {start}–{latest}, inside no quiet range: {_named('quiet')}"
        )


def _exceptions() -> dict[str, list[str]]:
    """Each ``exception`` line's key, mapped to its review-by date and reason."""
    return {words[0]: words[1:] for words in _named("exception")}


def _uv_run_timer_services() -> set[str]:
    """Each tracked service a tracked timer starts whose command is ``uv run``."""
    services = set()
    for timer in INFRA.glob("*.timer"):
        service = INFRA / timer.name.replace(".timer", ".service")
        text = service.read_text(encoding="utf-8") if service.is_file() else ""
        if re.search(r"^ExecStart=uv run ", text, re.MULTILINE):
            services.add(service.name)
    return services


def test_each_uv_run_timer_service_is_not_a_restarter() -> None:
    services = _uv_run_timer_services()
    assert services, "no uv-run timer service found in infra/: the parse is broken"
    declared = {
        key.removeprefix("not-restarter:")
        for key in _exceptions()
        if key.startswith("not-restarter:")
    }
    assert services <= declared, f"missing not-restarter lines: {sorted(services - declared)}"


def test_the_failure_notifier_is_not_a_restarter() -> None:
    assert "not-restarter:power-map-notify-failure@.service" in _exceptions()


def test_the_prunes_are_declared_and_none_is_kept() -> None:
    keys = set(_exceptions())
    for name, stage in PRUNED.items():
        assert f"{stage}:{name}" in keys, f"no {stage}:{name} line"
        assert f"keep:{name}" not in keys


def test_fwupd_is_held_while_it_soaks() -> None:
    assert "held:fwupd" in _exceptions()


def test_each_third_party_origin_is_followed() -> None:
    assert {tuple(words) for words in _named("origin")} == {(site, "follow") for site in ORIGINS}


def test_every_exception_has_a_date_and_a_reason() -> None:
    for key, rest in _exceptions().items():
        assert len(rest) >= 2, f"exception {key}: needs <review-by> <reason>"
        assert re.fullmatch(r"\d{4}-\d\d-\d\d", rest[0]), f"exception {key}: {rest[0]}"
