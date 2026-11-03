"""The pre-restart reachability check: scripts/pre-restart-check.sh (#748).

A restart while the database is unreachable does not fail: `ExecStartPre`'s
`apply-schema.sh` hangs on its connect, each attempt takes ~65 s, the start
limit never trips, and the unit loops in `start-pre` until the database comes
back — then applies any pending schema with nobody watching. The check runs
before every documented restart and refuses while the probe cannot connect.

These tests run the real scripts (the probe needs this checkout's venv), with
an environment that never inherits a real DSN. "The DB is down" is played two
ways: a closed port (refused at once) and a listener that accepts and never
answers — the 2026-11-01 shape, where the connect hangs until its timeout.
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
CHECK = REPO / "scripts" / "pre-restart-check.sh"
SECRET = "s3kr3t"
TRIAGE = "docs/RUNBOOK_DB_TRIAGE.md"


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


def _closed_port() -> int:
    """A localhost port with nothing listening on it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def refused_dsn() -> str:
    """A DSN whose connect is refused at once."""
    return f"postgresql://check_user:{SECRET}@127.0.0.1:{_closed_port()}/checkdb"


@pytest.fixture
def hanging_dsn() -> Iterator[str]:
    """A DSN whose server accepts the TCP connection and never says a word."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            held.append(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    port = server.getsockname()[1]
    try:
        yield f"postgresql://check_user:{SECRET}@127.0.0.1:{port}/checkdb"
    finally:
        stop.set()
        thread.join(timeout=2)
        for conn in held:
            conn.close()
        server.close()


@pytest.fixture
def ready_503() -> Iterator[str]:
    """A stand-in for a running API whose /ready fails on its pool."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
            body = json.dumps({"status": "unavailable", "reason": "pool_timeout"}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/ready"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _service_down() -> str:
    """A /ready URL with nothing listening: `:8000` is down."""
    return f"http://127.0.0.1:{_closed_port()}/ready"


def _env(tmp_path: Path, dsn: str | None, ready_url: str, **over: str) -> dict[str, str]:
    """Minimal environment — never inherits the real DSNs."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "POWER_MAP_ENV_FILE": str(tmp_path / "absent.env"),
        "POWER_MAP_READY_URL": ready_url,
        "POWER_MAP_PROBE_TIMEOUT": "1",
    }
    if dsn is not None:
        env["MIGRATIONS_DATABASE_URL"] = dsn
    env.update(over)
    return env


def _run(tmp_path: Path, *args: str, dsn: str | None, ready_url: str, **over: str):
    return subprocess.run(
        ["bash", str(CHECK), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_env(tmp_path, dsn, ready_url, **over),
        timeout=60,
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# Refusal — the database cannot be reached
# --------------------------------------------------------------------------- #


def test_refused_connect_refuses_the_restart(tmp_path, refused_dsn):
    result = _run(tmp_path, dsn=refused_dsn, ready_url=_service_down())

    assert result.returncode == 3, _out(result)
    assert "refusing" in _out(result)


def test_hanging_connect_refuses_within_the_probe_timeout(tmp_path, hanging_dsn):
    """The outage shape: the connect neither succeeds nor fails, it hangs."""
    started = time.monotonic()
    result = _run(tmp_path, dsn=hanging_dsn, ready_url=_service_down())
    elapsed = time.monotonic() - started

    assert result.returncode == 3, _out(result)
    assert "TimeoutError" in _out(result)
    # Bounded by POWER_MAP_PROBE_TIMEOUT=1, not asyncpg's 60 s default.
    assert elapsed < 20


def test_refusal_names_the_triage_runbook(tmp_path, refused_dsn):
    result = _run(tmp_path, dsn=refused_dsn, ready_url=_service_down())

    assert TRIAGE in _out(result)


def test_refusal_names_the_override(tmp_path, refused_dsn):
    result = _run(tmp_path, dsn=refused_dsn, ready_url=_service_down())

    assert "--force" in _out(result)


def test_refusal_never_prints_the_password(tmp_path, refused_dsn):
    result = _run(tmp_path, dsn=refused_dsn, ready_url=_service_down())

    assert result.returncode == 3, _out(result)
    assert SECRET not in _out(result)


def test_refusal_never_prints_the_dsn(tmp_path, hanging_dsn):
    """The exception's class is reported, never its message or the DSN."""
    result = _run(tmp_path, dsn=hanging_dsn, ready_url=_service_down())

    assert result.returncode == 3, _out(result)
    assert hanging_dsn not in _out(result)
    assert SECRET not in _out(result)
    assert "Traceback" not in _out(result)


def test_service_down_is_reported_and_does_not_decide(tmp_path, refused_dsn):
    """With :8000 down the check still reaches its verdict, from the probe."""
    result = _run(tmp_path, dsn=refused_dsn, ready_url=_service_down())

    assert "not answering" in _out(result)
    assert result.returncode == 3


def test_failing_ready_reason_is_reported(tmp_path, refused_dsn, ready_503):
    result = _run(tmp_path, dsn=refused_dsn, ready_url=ready_503)

    assert result.returncode == 3, _out(result)
    assert "pool_timeout" in _out(result)


def test_missing_dsn_refuses_rather_than_passing(tmp_path):
    """No DSN means the restart's own apply fails too — never a green light."""
    result = _run(tmp_path, dsn=None, ready_url=_service_down())

    assert result.returncode == 1, _out(result)
    assert "refusing" in _out(result)


# --------------------------------------------------------------------------- #
# Override
# --------------------------------------------------------------------------- #


def test_force_proceeds_past_an_unreachable_database(tmp_path, refused_dsn):
    result = _run(tmp_path, "--force", dsn=refused_dsn, ready_url=_service_down())

    assert result.returncode == 0, _out(result)
    assert "override" in _out(result)


def test_force_still_reports_what_it_overrode(tmp_path, refused_dsn):
    result = _run(tmp_path, "--force", dsn=refused_dsn, ready_url=_service_down())

    assert "unreachable" in _out(result)


def test_unknown_flag_exits_1(tmp_path, refused_dsn):
    result = _run(tmp_path, "--bogus", dsn=refused_dsn, ready_url=_service_down())

    assert result.returncode == 1
    assert "usage" in _out(result)


def test_help_goes_to_stdout(tmp_path):
    result = _run(tmp_path, "--help", dsn=None, ready_url=_service_down())

    assert result.returncode == 0
    assert "usage" in result.stdout


# --------------------------------------------------------------------------- #
# Recovery path — the database is back
# --------------------------------------------------------------------------- #


def _test_dsn() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    return dsn


@pytest.mark.integration
def test_reachable_database_passes_with_the_service_down(tmp_path):
    """The stuck-unit recovery: :8000 down (start-pre loop), DB back → go."""
    result = _run(
        tmp_path, dsn=_test_dsn(), ready_url=_service_down(), POWER_MAP_PROBE_TIMEOUT="10"
    )

    assert result.returncode == 0, _out(result)
    assert "not answering" in _out(result)
    assert "reachable" in _out(result)


@pytest.mark.integration
def test_reachable_database_passes_while_ready_still_fails(tmp_path, ready_503):
    """RUNBOOK_DB_TRIAGE § credential rotation: /ready fails and the restart is the fix."""
    result = _run(tmp_path, dsn=_test_dsn(), ready_url=ready_503, POWER_MAP_PROBE_TIMEOUT="10")

    assert result.returncode == 0, _out(result)
    assert "pool_timeout" in _out(result)


@pytest.mark.integration
def test_reachable_database_output_carries_no_password(tmp_path):
    dsn = _test_dsn()
    password = dsn.split("://", 1)[1].split("@", 1)[0].partition(":")[2]
    result = _run(tmp_path, dsn=dsn, ready_url=_service_down(), POWER_MAP_PROBE_TIMEOUT="10")

    assert not password or password not in _out(result)


# --------------------------------------------------------------------------- #
# Every documented restart runs the check first
# --------------------------------------------------------------------------- #

# `systemctl restart|start power-map` — the service itself, not a timer or
# helper unit named `power-map-*`.
_RESTART = re.compile(r"systemctl\s+(?:restart|start)\s+power-map(?![\w-])")
# docs/plans/ is the design record: it says what was run then, not what to run.
_HISTORICAL = REPO / "docs" / "plans"


def _live_docs() -> list[Path]:
    docs = [p for p in (REPO / "docs").rglob("*.md") if _HISTORICAL not in p.parents]
    return [REPO / "AGENTS.md", *docs, *(REPO / "infra").glob("*.service")]


def _restart_lines() -> list[tuple[str, str]]:
    return [
        (f"{path.relative_to(REPO)}:{n}", line)
        for path in _live_docs()
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _RESTART.search(line)
    ]


def test_every_documented_restart_runs_the_check():
    unchecked = [where for where, line in _restart_lines() if "pre-restart-check.sh" not in line]

    assert not unchecked, f"restart without the #748 pre-restart check: {unchecked}"


def test_the_restart_sweep_is_not_vacuous():
    """A regex that stopped matching would pass the sweep above by finding nothing."""
    files = {where.rsplit(":", 1)[0] for where, _ in _restart_lines()}

    assert {
        "AGENTS.md",
        "docs/COMMANDS.md",
        "docs/RUNBOOK_DB_TRIAGE.md",
        "docs/ALERTING.md",
        "docs/RUNBOOK_DB_MIGRATION.md",
        "infra/power-map.service",
    } <= files
