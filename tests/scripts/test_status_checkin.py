"""Tests for scripts.status_checkin — the timers' co-status check-in hook (#566).

Every ``power-map-*.timer`` service runs this as ``ExecStopPost=``. It turns
systemd's verdict on the run into one check-in: ``ok`` when systemd counted the
run a success (a declared ``SuccessExitStatus=`` warning included), ``alert``
otherwise. Three properties carry the design:

1. **The mapping.** ``SERVICE_RESULT`` decides ``ok`` versus ``alert``; a named
   script exit (``STATUS_EXIT_KINDS``) refines the ``kind`` of an ``exit-code``
   failure; ``metadata.fault`` mirrors ``kind`` on every alert so that a change
   of failure reports at once (status#28).
2. **Best-effort.** Nothing here may change the unit's result: every path
   returns 0, Status unreachable included. Silence then ends in ``missing``.
3. **Public repo.** The body is a summary plus the journal command, never
   journal text; the key never reaches a log line.

The subprocess tests run the file the way the unit does — ``/usr/bin/python3``
on the path, no venv — against a real local HTTP server.
"""

import json
import shutil
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scripts.status_checkin import (
    CREDENTIAL,
    DEFAULT_URL,
    build_payload,
    main,
    outcome,
    parse_exit_kinds,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "status_checkin.py"
UNIT = "power-map-schema-parity.service"
KEY = "sk_test_never_logged_0123456789"
MONITOR = "01K7TESTMONITOR0000000000A"


def _env(**overrides: str) -> dict[str, str]:
    env = {"SERVICE_RESULT": "success", "EXIT_CODE": "exited", "EXIT_STATUS": "0"}
    env.update(overrides)
    return env


# --- the mapping ------------------------------------------------------------


def test_a_clean_run_is_ok():
    result = outcome(UNIT, _env())

    assert (result.status, result.kind) == ("ok", "ok")
    assert UNIT in result.body


def test_a_declared_warning_exit_is_ok_and_says_so():
    """``SuccessExitStatus=4`` makes systemd report ``success`` with status 4."""
    result = outcome(UNIT, _env(EXIT_STATUS="4"))

    assert (result.status, result.kind) == ("ok", "ok")
    assert "4" in result.body
    assert f"journalctl -u {UNIT}" in result.body


def test_an_exit_code_failure_alerts_with_systemds_result():
    result = outcome(UNIT, _env(SERVICE_RESULT="exit-code", EXIT_STATUS="1"))

    assert (result.status, result.kind) == ("alert", "exit-code")
    assert result.body.startswith(f"{UNIT} failed: exit-code (exited 1). ")


def test_a_named_exit_refines_the_kind():
    result = outcome(
        UNIT,
        _env(SERVICE_RESULT="exit-code", EXIT_STATUS="3", STATUS_EXIT_KINDS="3:drift,4:untagged"),
    )

    assert (result.status, result.kind) == ("alert", "drift")
    assert result.body.startswith(f"{UNIT} failed: drift (exit-code, exited 3). ")


def test_an_unnamed_exit_keeps_systemds_result():
    result = outcome(
        UNIT, _env(SERVICE_RESULT="exit-code", EXIT_STATUS="1", STATUS_EXIT_KINDS="3:drift")
    )

    assert result.kind == "exit-code"


def test_a_named_exit_never_turns_a_success_into_an_alert():
    """The map refines failures only; systemd's verdict decides ok vs alert."""
    result = outcome(UNIT, _env(EXIT_STATUS="4", STATUS_EXIT_KINDS="4:reference-ahead"))

    assert (result.status, result.kind) == ("ok", "ok")


@pytest.mark.parametrize(
    ("result", "code", "status"),
    [("timeout", "killed", "TERM"), ("signal", "killed", "KILL"), ("core-dump", "dumped", "SEGV")],
)
def test_non_exit_failures_alert_with_systemds_result(result, code, status):
    """A signal's status is a name, never looked up in the exit map."""
    got = outcome(
        UNIT,
        _env(SERVICE_RESULT=result, EXIT_CODE=code, EXIT_STATUS=status, STATUS_EXIT_KINDS="9:x"),
    )

    assert (got.status, got.kind) == ("alert", result)
    assert status in got.body


def test_the_body_points_at_the_journal_and_carries_no_journal_text():
    result = outcome(UNIT, _env(SERVICE_RESULT="exit-code", EXIT_STATUS="3"))

    assert result.body.endswith(f"journalctl -u {UNIT} -n 100")
    assert "\n" not in result.body


def test_parse_exit_kinds_ignores_malformed_entries():
    assert parse_exit_kinds("3:drift, 4:untagged,,bad,x:y,5:") == {"3": "drift", "4": "untagged"}


# --- the payload ------------------------------------------------------------


def test_an_ok_payload_carries_all_four_variables_and_no_fault():
    payload = build_payload(UNIT, outcome(UNIT, _env()), host="power-map")

    assert payload["status"] == "ok"
    assert set(payload["variables"]) == {"unit", "kind", "body", "host"}
    assert payload["variables"]["unit"] == UNIT
    assert payload["variables"]["host"] == "power-map"
    assert "metadata" not in payload


def test_an_alert_payload_sets_metadata_fault_to_the_kind():
    result = outcome(
        UNIT, _env(SERVICE_RESULT="exit-code", EXIT_STATUS="3", STATUS_EXIT_KINDS="3:drift")
    )
    payload = build_payload(UNIT, result, host="power-map")

    assert payload["status"] == "alert"
    assert payload["variables"]["kind"] == "drift"
    assert payload["metadata"] == {"fault": "drift"}


# --- main: best-effort, through a fake opener -------------------------------


class _Response:
    def __init__(self, status: int = 202):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return b"{}"


class _Opener:
    """Records requests; replays a scripted sequence of responses or errors."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or [_Response()]
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        nxt = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


@pytest.fixture
def creds(tmp_path):
    (tmp_path / CREDENTIAL).write_text(KEY + "\n")
    return tmp_path


def _main_env(creds, **overrides):
    return _env(**{"CREDENTIALS_DIRECTORY": str(creds), "STATUS_MONITOR_ID": MONITOR, **overrides})


def test_main_posts_one_checkin_to_the_monitor(creds):
    opener = _Opener()

    assert main([UNIT], _main_env(creds), opener=opener, sleep=lambda s: None) == 0

    [(request, timeout)] = opener.requests
    assert request.full_url == f"{DEFAULT_URL}/api/v1/monitors/{MONITOR}/checkin"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == KEY
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data)["status"] == "ok"
    assert timeout <= 10


def test_main_honours_status_url(creds):
    opener = _Opener()

    main(
        [UNIT],
        _main_env(creds, STATUS_URL="http://127.0.0.1:9/"),
        opener=opener,
        sleep=lambda s: None,
    )

    assert opener.requests[0][0].full_url.startswith("http://127.0.0.1:9/api/v1/monitors/")


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("refused"), TimeoutError(), OSError("no route")],
)
def test_status_unreachable_retries_once_then_returns_zero(creds, caplog, error):
    opener = _Opener(error)
    sleeps = []

    assert main([UNIT], _main_env(creds), opener=opener, sleep=sleeps.append) == 0

    assert len(opener.requests) == 2
    assert sleeps
    assert "check-in" in caplog.text and "not sent" in caplog.text


def test_a_5xx_retries_and_a_later_202_lands(creds):
    server_error = urllib.error.HTTPError(DEFAULT_URL, 503, "busy", {}, None)
    opener = _Opener(server_error, _Response(202))

    assert main([UNIT], _main_env(creds), opener=opener, sleep=lambda s: None) == 0
    assert len(opener.requests) == 2


def test_a_4xx_is_not_retried(creds, caplog):
    """A wrong key or monitor id answers the same way twice."""
    opener = _Opener(urllib.error.HTTPError(DEFAULT_URL, 401, "nope", {}, None))

    assert main([UNIT], _main_env(creds), opener=opener, sleep=lambda s: None) == 0
    assert len(opener.requests) == 1
    assert "401" in caplog.text


def test_the_key_never_reaches_the_log(creds, caplog):
    caplog.set_level("DEBUG")
    opener = _Opener(urllib.error.HTTPError(DEFAULT_URL, 403, "forbidden", {}, None))

    main([UNIT], _main_env(creds), opener=opener, sleep=lambda s: None)

    assert KEY not in caplog.text


def test_no_monitor_id_skips_without_a_request(creds, caplog):
    opener = _Opener()

    assert main([UNIT], _main_env(creds, STATUS_MONITOR_ID=""), opener=opener) == 0
    assert opener.requests == []
    assert "STATUS_MONITOR_ID" in caplog.text


def test_an_empty_credential_skips_without_a_request(tmp_path, caplog):
    """``SetCredential=status-checkin:`` is the fallback when the key file is absent."""
    (tmp_path / CREDENTIAL).write_text("")
    opener = _Opener()

    assert main([UNIT], _main_env(tmp_path), opener=opener) == 0
    assert opener.requests == []
    assert "key" in caplog.text


def test_no_credentials_directory_skips_without_a_request(caplog):
    opener = _Opener()

    assert main([UNIT], _env(STATUS_MONITOR_ID=MONITOR), opener=opener) == 0
    assert opener.requests == []


def test_no_service_result_is_not_a_systemd_run_and_skips(creds):
    opener = _Opener()
    env = _main_env(creds)
    del env["SERVICE_RESULT"]

    assert main([UNIT], env, opener=opener) == 0
    assert opener.requests == []


def test_test_flag_sends_a_test_alert_whatever_the_run(creds):
    opener = _Opener()
    env = _main_env(creds)
    del env["SERVICE_RESULT"]

    assert main([UNIT, "--test"], env, opener=opener, sleep=lambda s: None) == 0

    payload = json.loads(opener.requests[0][0].data)
    assert payload["status"] == "alert"
    assert payload["variables"]["kind"] == "test"
    assert payload["metadata"] == {"fault": "test"}


def test_bad_usage_returns_zero(caplog):
    assert main([], {}, opener=_Opener()) == 0
    assert "usage" in caplog.text


def test_an_unexpected_error_still_returns_zero(creds):
    def boom(request, timeout):
        raise RuntimeError("bug")

    assert main([UNIT], _main_env(creds), opener=boom, sleep=lambda s: None) == 0


def test_the_monitor_id_is_quoted_into_the_path(creds):
    opener = _Opener()

    main([UNIT], _main_env(creds, STATUS_MONITOR_ID="../x"), opener=opener, sleep=lambda s: None)

    assert opener.requests[0][0].full_url.endswith("/api/v1/monitors/..%2Fx/checkin")


# --- through the seam: the file under the system interpreter ----------------


class _Recorder(BaseHTTPRequestHandler):
    received: list = []
    status = 202

    def do_POST(self):  # noqa: N802 - http.server's naming
        length = int(self.headers["Content-Length"])
        type(self).received.append(
            (self.path, self.headers["X-API-Key"], json.loads(self.rfile.read(length)))
        )
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def status_server():
    _Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", _Recorder.received
    server.shutdown()


def _system_python() -> str:
    python = shutil.which("python3", path="/usr/bin") or sys.executable
    return python


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_system_python(), str(SCRIPT), UNIT, *args],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=60,
        cwd="/",
        check=False,
    )


def test_the_script_runs_standalone_and_posts_an_alert(status_server, creds):
    """No venv, no repo on sys.path, cwd elsewhere: what ``ExecStopPost=`` gets."""
    url, received = status_server

    proc = _run(
        _main_env(
            creds,
            STATUS_URL=url,
            SERVICE_RESULT="exit-code",
            EXIT_STATUS="3",
            STATUS_EXIT_KINDS="3:drift",
        )
    )

    assert proc.returncode == 0, proc.stderr
    [(path, key, body)] = received
    assert path == f"/api/v1/monitors/{MONITOR}/checkin"
    assert key == KEY
    assert body["status"] == "alert"
    assert body["variables"]["kind"] == "drift"
    assert KEY not in proc.stderr + proc.stdout


def test_the_script_exits_zero_when_status_is_down(creds):
    proc = _run(_main_env(creds, STATUS_URL="http://127.0.0.1:9"))

    assert proc.returncode == 0
    assert "not sent" in proc.stderr
