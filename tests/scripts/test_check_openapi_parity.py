"""Tests for scripts.check_openapi_parity — the live-schema backstop (#618).

The commit-time gate (``tests/clients/python/test_drift.py``) keeps the snapshot
in step with the code. This guard checks what that gate cannot see: the schema
the running workers actually serve. It catches a schema that depends on
runtime configuration, a deploy that skipped the gate, and a pull that was never
followed by a restart. HTTP-only, read-only. Exit 3 on a difference, so the unit
lands in ``systemctl --failed``.
"""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from scripts.check_openapi_parity import DEFAULT_URL, differences, main

ROOT = Path(__file__).resolve().parents[2]

_COMMITTED = {
    "openapi": "3.1.0",
    "info": {"title": "power-map", "version": "1.2.3"},
    "paths": {"/health": {"get": {}}, "/api/v1/orgs": {"get": {}}},
    "components": {"schemas": {"OrgDetail": {"type": "object"}}},
}


def _opener(body: object):
    def opener(url, timeout):
        assert url == "http://pm.test/openapi.json"
        return io.BytesIO(json.dumps(body).encode())

    return opener


def _unreachable(url, timeout):
    raise urllib.error.URLError("connection refused")


def _run(monkeypatch, tmp_path, opener) -> int:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(json.dumps(_COMMITTED, indent=2))
    argv = ["check_openapi_parity", "--url", "http://pm.test/openapi.json"]
    monkeypatch.setattr(sys, "argv", [*argv, "--snapshot", str(snapshot)])
    with pytest.raises(SystemExit) as exc:
        main(opener=opener)
    return exc.value.code


# --- differences ------------------------------------------------------------


def test_identical_schemas_have_no_differences():
    assert differences(_COMMITTED, json.loads(json.dumps(_COMMITTED))) == []


def test_a_version_mismatch_is_named():
    live = {**_COMMITTED, "info": {"title": "power-map", "version": "1.2.2"}}
    assert differences(live, _COMMITTED) == ["info.version: live 1.2.2, committed 1.2.3"]


def test_added_and_removed_paths_are_named():
    live = {**_COMMITTED, "paths": {"/health": {"get": {}}, "/admin/": {"get": {}}}}
    found = differences(live, _COMMITTED)
    assert "paths only live: /admin/" in found
    assert "paths only committed: /api/v1/orgs" in found


def test_a_changed_operation_and_schema_are_named():
    live = json.loads(json.dumps(_COMMITTED))
    live["paths"]["/health"]["get"]["operationId"] = "getHealth"
    live["components"]["schemas"]["OrgDetail"]["required"] = ["id"]
    found = differences(live, _COMMITTED)
    assert "paths differ: /health" in found
    assert "components.schemas differ: OrgDetail" in found


def test_any_other_difference_is_still_reported():
    live = {**_COMMITTED, "openapi": "3.0.0"}
    assert differences(live, _COMMITTED) == ["top-level keys differ: openapi"]


# --- main --------------------------------------------------------------------


def test_main_exits_0_when_live_matches(monkeypatch, tmp_path, capsys):
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED)) == 0
    assert "matches" in capsys.readouterr().out


def test_main_exits_3_on_a_difference(monkeypatch, tmp_path, capsys):
    """The journal names what differs: it is the first thing an operator reads."""
    live = {**_COMMITTED, "info": {"title": "power-map", "version": "9.9.9"}}
    assert _run(monkeypatch, tmp_path, _opener(live)) == 3
    assert "info.version: live 9.9.9, committed 1.2.3" in capsys.readouterr().out


def test_main_exits_1_when_the_server_is_unreachable(monkeypatch, tmp_path, capsys):
    """Not a drift finding: /ready's own guard owns liveness. Still a failure."""
    assert _run(monkeypatch, tmp_path, _unreachable) == 1
    assert "could not fetch" in capsys.readouterr().out


def test_defaults_read_production_and_the_committed_snapshot():
    assert DEFAULT_URL == "http://localhost:8000/openapi.json"


# --- the unit files ----------------------------------------------------------


def test_service_runs_the_guard_read_only_from_the_main_checkout():
    unit = (ROOT / "infra" / "power-map-openapi-parity.service").read_text()
    assert "Type=oneshot" in unit
    assert "WorkingDirectory=/home/exedev/power-map" in unit
    assert "ExecStart=uv run python -m scripts.check_openapi_parity" in unit


def test_timer_is_daily_and_persistent():
    timer = (ROOT / "infra" / "power-map-openapi-parity.timer").read_text()
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_long_lists_are_capped_with_a_count():
    """A wholesale drift (e.g. the 300 admin paths) stays one readable line."""
    live = {**_COMMITTED, "paths": {f"/p{i:02}": {} for i in range(25)}}
    line = differences(live, {**_COMMITTED, "paths": {}})[0]
    assert line.startswith("paths only live: /p00, /p01,")
    assert line.endswith("/p09 (+15 more)")


def test_main_exits_1_when_a_body_is_not_a_json_object(monkeypatch, tmp_path, capsys):
    """A proxy error page can be valid JSON and still not a schema."""
    assert _run(monkeypatch, tmp_path, _opener("Bad Gateway")) == 1
    assert "not a JSON object" in capsys.readouterr().out


@pytest.mark.parametrize("timeout", ["0", "-5"])
def test_main_rejects_a_non_positive_timeout(monkeypatch, timeout):
    monkeypatch.setattr(sys, "argv", ["check_openapi_parity", "--timeout", timeout])
    with pytest.raises(SystemExit) as exc:
        main(opener=_unreachable)
    assert exc.value.code == 2
