"""Tests for scripts.check_openapi_parity — the live-schema backstop (#618).

The commit-time gate (``tests/clients/python/test_drift.py``) keeps the snapshot
in step with the code. This guard checks what that gate cannot see: the schema
the running workers actually serve. It catches a schema that depends on
runtime configuration, a deploy that skipped the gate, and a pull that was never
followed by a restart. HTTP-only, read-only. Exit 3 on a difference, so the unit
lands in ``systemctl --failed``.

#631 adds the release-tag check: ``v<served version>`` must exist on GitHub and
point at a commit whose snapshot equals the served schema. Exit 4 otherwise,
unless the service started inside the grace window or GitHub can't be reached.
"""

import io
import json
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.check_openapi_parity import (
    DEFAULT_URL,
    GRACE,
    differences,
    main,
    service_started_at,
)

ROOT = Path(__file__).resolve().parents[2]

_COMMITTED = {
    "openapi": "3.1.0",
    "info": {"title": "power-map", "version": "1.2.3"},
    "paths": {"/health": {"get": {}}, "/api/v1/orgs": {"get": {}}},
    "components": {"schemas": {"OrgDetail": {"type": "object"}}},
}


_LIVE_URL = "http://pm.test/openapi.json"
_API = "https://api.github.com/repos/CannObserv/power-map/git"
_SHA = "a" * 40
_TAG_SHA = "b" * 40


def _ref_url(version: str = "1.2.3") -> str:
    return f"{_API}/ref/tags/v{version}"


def _raw_url(sha: str = _SHA) -> str:
    return (
        f"https://raw.githubusercontent.com/CannObserv/power-map/{sha}/clients/python/openapi.json"
    )


def _lightweight(sha: str = _SHA) -> dict:
    return {"ref": "refs/tags/v1.2.3", "object": {"sha": sha, "type": "commit"}}


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "status", {}, None)


def _tagged(schema: object = _COMMITTED, sha: str = _SHA) -> dict:
    """GitHub as it is when v1.2.3 is a lightweight tag on a commit carrying *schema*."""
    return {_ref_url(): _lightweight(sha), _raw_url(sha): schema}


def _opener(body: object, github: dict | None = None):
    """Serve *body* as the live schema and *github* as the GitHub responses.

    A value that is an exception is raised; a URL missing from *github* is a 404,
    so a test that forgets a response fails loudly as "untagged", not silently.
    """
    responses = {_LIVE_URL: body, **(_tagged() if github is None else github)}

    def opener(url, timeout):
        if url not in responses:
            raise _http_error(url, 404)
        value = responses[url]
        if isinstance(value, BaseException):
            raise value
        return io.BytesIO(json.dumps(value).encode())

    return opener


def _unreachable(url, timeout):
    raise urllib.error.URLError("connection refused")


def _long_ago():
    return datetime.now(UTC) - timedelta(days=3)


def _run(monkeypatch, tmp_path, opener, started_at=_long_ago, extra=()) -> int:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(json.dumps(_COMMITTED, indent=2))
    argv = ["check_openapi_parity", "--url", _LIVE_URL, *extra]
    monkeypatch.setattr(sys, "argv", [*argv, "--snapshot", str(snapshot)])
    with pytest.raises(SystemExit) as exc:
        main(opener=opener, started_at=started_at)
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
    github = {_ref_url("9.9.9"): _lightweight(), _raw_url(): live}
    assert _run(monkeypatch, tmp_path, _opener(live, github)) == 3
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


# --- the release tag (#631) ---------------------------------------------------


def test_main_exits_0_when_the_tag_is_on_a_matching_commit(monkeypatch, tmp_path, capsys):
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED)) == 0
    assert f"v1.2.3 at {_SHA[:12]} matches" in capsys.readouterr().out


def test_an_annotated_tag_is_followed_to_its_commit(monkeypatch, tmp_path):
    github = {
        _ref_url(): {"object": {"sha": _TAG_SHA, "type": "tag"}},
        f"{_API}/tags/{_TAG_SHA}": {"object": {"sha": _SHA, "type": "commit"}},
        _raw_url(): _COMMITTED,
    }
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github)) == 0


def test_main_exits_4_when_the_version_is_untagged(monkeypatch, tmp_path, capsys):
    """The journal names the fix: it is the first thing an operator reads."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github={})) == 4
    out = capsys.readouterr().out
    assert "v1.2.3 is not tagged on CannObserv/power-map" in out
    assert "git tag v1.2.3 && git push origin v1.2.3" in out


def test_main_exits_4_when_the_tag_is_on_a_commit_with_another_schema(
    monkeypatch, tmp_path, capsys
):
    """A tag pushed at the wrong ref is caught, not only a missing one."""
    older = {**_COMMITTED, "paths": {"/health": {"get": {}}}}
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, _tagged(older))) == 4
    out = capsys.readouterr().out
    assert f"v1.2.3 points at {_SHA[:12]}" in out
    assert "differs from the served schema" in out
    assert "git push -f origin v1.2.3" in out


def test_main_exits_4_when_the_tagged_commit_has_no_snapshot(monkeypatch, tmp_path, capsys):
    github = {_ref_url(): _lightweight()}
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github)) == 4
    assert "has no clients/python/openapi.json" in capsys.readouterr().out


def test_schema_drift_outranks_a_tag_finding_and_both_are_logged(monkeypatch, tmp_path, capsys):
    live = {**_COMMITTED, "openapi": "3.0.0"}
    assert _run(monkeypatch, tmp_path, _opener(live, github={})) == 3
    out = capsys.readouterr().out
    assert "top-level keys differ: openapi" in out
    assert "v1.2.3 is not tagged" in out


def test_a_fresh_deploy_inside_the_grace_window_skips_the_tag_check(monkeypatch, tmp_path, capsys):
    """The timer runs daily: a deploy minutes before it can't have been tagged yet."""

    def just_started():
        return datetime.now(UTC) - GRACE + timedelta(minutes=5)

    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github={}), just_started) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    skip = [line for line in lines if "grace window" in line["message"]]
    assert [line["level"] for line in skip] == ["INFO"]
    assert "release tag not checked" in skip[0]["message"]


def test_an_unknown_start_time_still_checks_the_tag(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github={}), lambda: None) == 4


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("name resolution failed"),
        _http_error(_ref_url(), 403),
        _http_error(_ref_url(), 503),
        TimeoutError("timed out"),
    ],
    ids=["unreachable", "rate-limited", "unavailable", "timeout"],
)
def test_github_trouble_skips_only_the_tag_check(monkeypatch, tmp_path, capsys, failure):
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, {_ref_url(): failure})) == 0
    assert "release tag not checked" in capsys.readouterr().out


def test_github_trouble_does_not_hide_schema_drift(monkeypatch, tmp_path):
    live = {**_COMMITTED, "openapi": "3.0.0"}
    failure = urllib.error.URLError("unreachable")
    assert _run(monkeypatch, tmp_path, _opener(live, {_ref_url(): failure})) == 3


def test_an_unexpected_github_body_skips_the_tag_check(monkeypatch, tmp_path, capsys):
    """GitHub answering 200 with something other than a ref is not a finding."""
    github = {_ref_url(): ["not", "a", "ref"]}
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github)) == 0
    assert "release tag not checked" in capsys.readouterr().out


def test_a_served_schema_without_a_version_skips_the_tag_check(monkeypatch, tmp_path, capsys):
    unversioned = {k: v for k, v in _COMMITTED.items() if k != "info"}

    def opener(url, timeout):
        assert url == _LIVE_URL, "no version, so GitHub is never asked"
        return io.BytesIO(json.dumps(unversioned).encode())

    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(json.dumps(unversioned))
    monkeypatch.setattr(
        sys, "argv", ["check_openapi_parity", "--url", _LIVE_URL, "--snapshot", str(snapshot)]
    )
    with pytest.raises(SystemExit) as exc:
        main(opener=opener, started_at=_long_ago)
    assert exc.value.code == 0
    assert "no info.version" in capsys.readouterr().out


def test_no_tag_check_never_asks_github(monkeypatch, tmp_path):
    """For a dev server on 8001, whose version is often bumped and untagged."""

    def opener(url, timeout):
        assert url == _LIVE_URL
        return io.BytesIO(json.dumps(_COMMITTED).encode())

    assert _run(monkeypatch, tmp_path, opener, extra=["--no-tag-check"]) == 0


# --- service_started_at ---------------------------------------------------------


def _completed(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_service_started_at_reads_systemd_unix_timestamp():
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return _completed("@1791561082\n")

    assert service_started_at(run=run) == datetime.fromtimestamp(1791561082, UTC)
    assert calls == [
        [
            "systemctl",
            "show",
            "power-map.service",
            "--property=ActiveEnterTimestamp",
            "--timestamp=unix",
            "--value",
        ]
    ]


@pytest.mark.parametrize(
    "result",
    [_completed("\n"), _completed("n/a\n"), _completed("", returncode=1)],
    ids=["inactive", "unparseable", "failed"],
)
def test_service_started_at_is_none_when_unknown(result):
    assert service_started_at(run=lambda cmd, **kwargs: result) is None


@pytest.mark.parametrize(
    "error", [FileNotFoundError("systemctl"), subprocess.TimeoutExpired("systemctl", 10)]
)
def test_service_started_at_is_none_without_systemctl(error):
    def run(cmd, **kwargs):
        raise error

    assert service_started_at(run=run) is None


def test_grace_is_about_two_hours():
    assert GRACE == timedelta(hours=2)


@pytest.mark.parametrize(
    "tag_object",
    [{"object": "not-a-mapping"}, _http_error(f"{_API}/tags/{_TAG_SHA}", 404)],
    ids=["malformed", "missing"],
)
def test_a_broken_annotated_tag_skips_the_check(monkeypatch, tmp_path, capsys, tag_object):
    """The ref exists, so a bad tag object is GitHub's oddity: never "not tagged", never a crash."""
    github = {
        _ref_url(): {"object": {"sha": _TAG_SHA, "type": "tag"}},
        f"{_API}/tags/{_TAG_SHA}": tag_object,
    }
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github)) == 0
    out = capsys.readouterr().out
    assert "release tag not checked" in out
    assert "is not tagged" not in out
