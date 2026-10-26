"""Check a timer's run in with co-status, ``ok`` or ``alert`` (issue #566).

The ``ExecStopPost=`` hook on every ``power-map-*.timer`` service. co-status
(CannObserv/status#36, tenant ``co-power-map``) holds one dead-man monitor per
timer: a run that fails reports through Notifier, and a run that never happens
(timer disabled, unit not reinstalled, VM down) goes ``missing`` there.

systemd sets ``SERVICE_RESULT``, ``EXIT_CODE`` and ``EXIT_STATUS`` for
``ExecStopPost=``. ``SERVICE_RESULT=success`` is ``ok``, and that includes a
declared warning exit (``SuccessExitStatus=4``). Anything else is ``alert``, its
``kind`` systemd's result, or the script's own name for the exit where the unit
gives one (``STATUS_EXIT_KINDS=3:drift,4:untagged``). Every alert carries
``metadata.fault = kind``, so a change of failure reports at once (status#28).

Never alters the unit's result: every path returns 0, and the unit prefixes the
command with ``-`` as well. A check-in that cannot be sent is logged and
dropped; the monitor's silence then ends in ``missing``. The body is a summary
and the journal command, never journal text: this repo is public, and alert
bodies reach places it does not control.

**Standard library only, run by ``/usr/bin/python3`` straight from the file.**
The alert matters most when the venv or ``uv`` is what broke, so this file
imports nothing from the repo but its stdlib sibling ``_journal.py``, and logs
with plain ``logging`` to stderr (the journal) instead of ``src.core.logging``.
Each line carries its syslog priority on the journal (#692), so a check-in
that was not sent shows in ``journalctl -p warning``.

Usage (in a unit):
    ExecStopPost=-/usr/bin/python3 /home/exedev/power-map/scripts/status_checkin.py %n

A deliberate test alert (``kind=test``); the timer's next ``ok`` clears it:
    sudo systemd-run --wait --pipe \\
      -p LoadCredential=status-checkin:/etc/power-map/status-api.key \\
      -E STATUS_MONITOR_ID=<id> \\
      /usr/bin/python3 /home/exedev/power-map/scripts/status_checkin.py <unit> --test

Environment:
    STATUS_MONITOR_ID       the unit's co-status monitor; empty = skip, logged
    STATUS_URL              default http://status:9000 (production co-status)
    STATUS_EXIT_KINDS       optional ``code:kind`` pairs, comma-separated
    CREDENTIALS_DIRECTORY   systemd's; holds ``status-checkin`` (empty = skip)
"""

import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

try:  # run as a file (the units): the script's directory is sys.path[0]
    import _journal
except ImportError:  # imported as scripts.<name> (tests, python -m)
    from scripts import _journal

logger = logging.getLogger("status_checkin")

DEFAULT_URL = "http://status:9000"
CREDENTIAL = "status-checkin"
# co-status answers inside an 8-second budget below a 10-second client timeout.
TIMEOUT_SECONDS = 10.0
ATTEMPTS = 2
RETRY_DELAY_SECONDS = 2.0
JOURNAL_LINES = 100


@dataclass(frozen=True)
class Outcome:
    """One run's verdict: check-in ``status``, the failure ``kind``, a one-line body."""

    status: str
    kind: str
    body: str


def parse_exit_kinds(raw: str) -> dict[str, str]:
    """``"3:drift,4:untagged"`` → ``{"3": "drift", "4": "untagged"}``; skips malformed pairs."""
    kinds: dict[str, str] = {}
    for pair in raw.split(","):
        code, _, kind = pair.strip().partition(":")
        if code.isdigit() and kind.strip():
            kinds[code] = kind.strip()
    return kinds


def _journal_command(unit: str) -> str:
    return f"journalctl -u {unit} -n {JOURNAL_LINES}"


def outcome(unit: str, env: Mapping[str, str]) -> Outcome:
    """Map systemd's verdict on the run to a check-in."""
    result = env.get("SERVICE_RESULT", "")
    exit_code = env.get("EXIT_CODE", "")
    exit_status = env.get("EXIT_STATUS", "")
    if result == "success":
        if exit_status not in ("", "0"):
            return Outcome(
                "ok",
                "ok",
                f"{unit} succeeded with its declared warning exit {exit_status}. "
                f"Read it on the host: {_journal_command(unit)}",
            )
        return Outcome("ok", "ok", f"{unit} ran clean.")

    kind = result or "unknown"
    if result == "exit-code" and exit_code == "exited":
        kind = parse_exit_kinds(env.get("STATUS_EXIT_KINDS", "")).get(exit_status, kind)
    # systemd's result only when the kind is the script's own name for the exit.
    parts = (result if kind != result else "", f"{exit_code} {exit_status}".strip())
    detail = ", ".join(part for part in parts if part)
    summary = f"{unit} failed: {kind} ({detail})" if detail else f"{unit} failed: {kind}"
    return Outcome("alert", kind, f"{summary}. Read it on the host: {_journal_command(unit)}")


def deliberate_alert(unit: str) -> Outcome:
    """A deliberate alert, for proving the path end to end."""
    return Outcome(
        "alert",
        "test",
        f"Deliberate test alert for {unit}; its next ok check-in clears it. "
        f"Read it on the host: {_journal_command(unit)}",
    )


def build_payload(unit: str, result: Outcome, *, host: str) -> dict:
    """The check-in body: all four variables always, ``metadata.fault`` on an alert."""
    payload: dict = {
        "status": result.status,
        "variables": {"unit": unit, "kind": result.kind, "body": result.body, "host": host},
    }
    if result.status == "alert":
        payload["metadata"] = {"fault": result.kind}
    return payload


def read_key(env: Mapping[str, str]) -> str:
    """The check-in key from systemd's credential directory, or "" when absent."""
    directory = env.get("CREDENTIALS_DIRECTORY", "")
    if not directory:
        return ""
    try:
        return (Path(directory) / CREDENTIAL).read_text().strip()
    except OSError:
        return ""


def post(
    url: str,
    key: str,
    payload: dict,
    *,
    opener: Callable,
    sleep: Callable[[float], None],
) -> int | None:
    """POST the check-in; return the HTTP status, or None when nothing answered.

    Retries once on no answer or a 5xx. A 4xx (wrong key, unknown monitor, a
    422) would answer the same way again, so it is returned at once.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    status: int | None = None
    for attempt in range(ATTEMPTS):
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                return getattr(response, "status", None) or 200
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status < 500:
                return status
        except (urllib.error.URLError, OSError) as exc:
            # TimeoutError is an OSError; URLError wraps the rest.
            logger.info("check-in attempt %d: %s", attempt + 1, type(exc).__name__)
            status = None
        if attempt < ATTEMPTS - 1:
            sleep(RETRY_DELAY_SECONDS)
    return status


def _checkin(
    args: list[str], env: Mapping[str, str], opener: Callable, sleep: Callable[[float], None]
) -> None:
    if not args or len(args) > 2 or (len(args) == 2 and args[1] != "--test"):
        logger.warning("usage: status_checkin.py <unit> [--test]")
        return
    unit = args[0]
    is_test = len(args) == 2
    if not is_test and not env.get("SERVICE_RESULT"):
        logger.warning("no SERVICE_RESULT: not run by systemd as ExecStopPost=; check-in skipped")
        return
    monitor_id = env.get("STATUS_MONITOR_ID", "").strip()
    if not monitor_id:
        logger.warning("STATUS_MONITOR_ID is unset for %s; check-in skipped", unit)
        return
    key = read_key(env)
    if not key:
        logger.warning("no %s key in CREDENTIALS_DIRECTORY; check-in skipped", CREDENTIAL)
        return

    result = deliberate_alert(unit) if is_test else outcome(unit, env)
    base = (env.get("STATUS_URL") or DEFAULT_URL).rstrip("/")
    url = f"{base}/api/v1/monitors/{urllib.parse.quote(monitor_id, safe='')}/checkin"
    payload = build_payload(unit, result, host=socket.gethostname())
    status = post(url, key, payload, opener=opener, sleep=sleep)
    if status is not None and 200 <= status < 300:
        logger.info(
            "check-in %s (%s) sent for %s: HTTP %d", result.status, result.kind, unit, status
        )
    else:
        answer = f"HTTP {status}" if status is not None else "no answer"
        logger.warning(
            "check-in %s (%s) for %s not sent: %s; the unit's result stands",
            result.status,
            result.kind,
            unit,
            answer,
        )


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    opener: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Always 0: a failing check-in must never fail the run it reports on."""
    args = sys.argv[1:] if argv is None else argv
    environ = os.environ if env is None else env
    try:
        _checkin(args, environ, opener, sleep)
    except Exception:
        logger.warning("check-in failed unexpectedly; the unit's result stands", exc_info=True)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, handlers=[_journal.log_handler(sys.stderr, os.environ)])
    sys.exit(main())
