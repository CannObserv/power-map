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

#824 resolves the tag with ``git ls-remote`` over https instead of GitHub's REST
API, whose anonymous quota this egress IP shares. The git call has its own seam
(``git=``), so no test here runs a network ``git ls-remote``; the local-repo
tests at the end run the real binary against a ``file://`` bare repo.
"""

import http.client
import io
import json
import os
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.check_openapi_parity import (
    DEFAULT_URL,
    GIT_URL,
    GRACE,
    differences,
    git_env,
    main,
    service_started_at,
    tagged_commit,
)

ROOT = Path(__file__).resolve().parents[2]

_COMMITTED = {
    "openapi": "3.1.0",
    "info": {"title": "power-map", "version": "1.2.3"},
    "paths": {"/health": {"get": {}}, "/api/v1/orgs": {"get": {}}},
    "components": {"schemas": {"OrgDetail": {"type": "object"}}},
}


_LIVE_URL = "http://pm.test/openapi.json"
_SHA = "a" * 40
_TAG_SHA = "b" * 40
_REF = "refs/tags/v1.2.3"


def _raw_url(sha: str = _SHA) -> str:
    return (
        f"https://raw.githubusercontent.com/CannObserv/power-map/{sha}/clients/python/openapi.json"
    )


def _lightweight(sha: str = _SHA, ref: str = _REF) -> str:
    """``git ls-remote`` output for a lightweight tag: the ref names the commit."""
    return f"{sha}\t{ref}\n"


def _annotated(sha: str = _SHA, tag_sha: str = _TAG_SHA, ref: str = _REF) -> str:
    """``git ls-remote`` output for an annotated tag: the tag object, then the peeled commit."""
    return f"{tag_sha}\t{ref}\n{sha}\t{ref}^{{}}\n"


def _git(stdout: str = "", returncode: int = 0, stderr: str = "", calls: list | None = None):
    """A stand-in for ``subprocess.run`` that answers every git call with one result."""

    def run(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run


def _git_raises(error: BaseException):
    def run(cmd, **kwargs):
        raise error

    return run


_ABSENT = _git(returncode=2)  # --exit-code: no ref matched


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "status", {}, None)


def _opener(body: object, github: dict | None = None):
    """Serve *body* as the live schema and *github* as raw.githubusercontent.com.

    The default *github* serves the committed schema at ``_SHA``. A value that is
    an exception is raised; a URL missing from *github* is a 404.
    """
    responses = {_LIVE_URL: body, **({_raw_url(): _COMMITTED} if github is None else github)}

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


def _never_git(cmd, **kwargs):
    raise AssertionError(f"git must not run here: {cmd}")


def _run(
    monkeypatch,
    tmp_path,
    opener,
    started_at=_long_ago,
    extra=(),
    committed=_COMMITTED,
    git=None,
) -> int:
    """Run the CLI; *git* defaults to a lightweight v1.2.3 on ``_SHA``, never real git."""
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(json.dumps(committed, indent=2))
    argv = ["check_openapi_parity", "--url", _LIVE_URL, *extra]
    monkeypatch.setattr(sys, "argv", [*argv, "--snapshot", str(snapshot)])
    with pytest.raises(SystemExit) as exc:
        main(opener=opener, started_at=started_at, git=git or _git(_lightweight()))
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
    git = _git(_lightweight(ref="refs/tags/v9.9.9"))
    assert _run(monkeypatch, tmp_path, _opener(live, {_raw_url(): live}), git=git) == 3
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
        main(opener=_unreachable, git=_never_git)
    assert exc.value.code == 2


# --- the release tag (#631, resolved with git ls-remote since #824) -----------


def test_main_exits_0_when_the_tag_is_on_a_matching_commit(monkeypatch, tmp_path, capsys):
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED)) == 0
    assert f"v1.2.3 at {_SHA[:12]} matches" in capsys.readouterr().out


def test_an_annotated_tag_resolves_to_its_peeled_commit(monkeypatch, tmp_path, capsys):
    """The ``^{}`` line is the commit; the tag object's own SHA is never fetched."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=_git(_annotated())) == 0
    assert f"v1.2.3 at {_SHA[:12]} matches" in capsys.readouterr().out


def test_a_lightweight_tag_passes_as_it_did_before(monkeypatch, tmp_path, capsys):
    """No new finding (#824): re-tagging a lightweight release is the operator's call."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=_git(_lightweight())) == 0
    out = capsys.readouterr().out
    assert f"v1.2.3 at {_SHA[:12]} matches" in out
    assert "lightweight tag" not in out  # tmp_path carries the test's name


def test_main_exits_4_when_the_version_is_untagged(monkeypatch, tmp_path, capsys):
    """The journal names the fix, an annotated tag: the release rule is ``git tag -a``."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=_ABSENT) == 4
    out = capsys.readouterr().out
    assert "v1.2.3 is not tagged on CannObserv/power-map" in out
    assert "git tag -a v1.2.3 -m v1.2.3 && git push origin v1.2.3" in out


def test_main_exits_4_when_the_tag_is_on_a_commit_with_another_schema(
    monkeypatch, tmp_path, capsys
):
    """A tag pushed at the wrong ref is caught, not only a missing one."""
    older = {**_COMMITTED, "paths": {"/health": {"get": {}}}}
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, {_raw_url(): older})) == 4
    out = capsys.readouterr().out
    assert f"v1.2.3 points at {_SHA[:12]}" in out
    assert "differs from the served schema" in out
    assert "git tag -a -f v1.2.3 -m v1.2.3 && git push -f origin v1.2.3" in out


def test_main_exits_4_when_the_tagged_commit_has_no_snapshot(monkeypatch, tmp_path, capsys):
    """Also what a tag on a tree or blob reads as: ls-remote can't say what it peels to."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, github={})) == 4
    assert "has no clients/python/openapi.json" in capsys.readouterr().out


def test_schema_drift_outranks_a_tag_finding_and_both_are_logged(monkeypatch, tmp_path, capsys):
    live = {**_COMMITTED, "openapi": "3.0.0"}
    assert _run(monkeypatch, tmp_path, _opener(live), git=_ABSENT) == 3
    out = capsys.readouterr().out
    assert "top-level keys differ: openapi" in out
    assert "v1.2.3 is not tagged" in out


def test_a_fresh_deploy_inside_the_grace_window_skips_the_tag_check(monkeypatch, tmp_path, capsys):
    """The timer runs daily: a deploy minutes before it can't have been tagged yet."""

    def just_started():
        return datetime.now(UTC) - GRACE + timedelta(minutes=5)

    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), just_started, git=_never_git) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    skip = [line for line in lines if "grace window" in line["message"]]
    assert [line["level"] for line in skip] == ["INFO"]
    assert "release tag not checked" in skip[0]["message"]


def test_an_unknown_start_time_still_checks_the_tag(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), lambda: None, git=_ABSENT) == 4


@pytest.mark.parametrize(
    "git",
    [
        _git(
            returncode=128,
            stderr="fatal: unable to access 'https://github.com/…': "
            "Could not resolve host: github.com\n",
        ),
        _git(
            returncode=128,
            stderr="fatal: could not read Username for "
            "'https://github.com': terminal prompts disabled\n",
        ),
        _git(returncode=1),
        _git_raises(subprocess.TimeoutExpired("git", 10)),
        _git_raises(FileNotFoundError(2, "No such file or directory", "git")),
        _git_raises(PermissionError(13, "Permission denied", "git")),
    ],
    ids=["unreachable", "auth-prompt", "other-exit", "timeout", "no-git", "not-executable"],
)
def test_git_trouble_skips_only_the_tag_check(monkeypatch, tmp_path, capsys, git):
    """WARNING, not INFO: the check was wanted and did not happen."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=git) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    skip = [line for line in lines if "release tag not checked" in line["message"]]
    assert [line["level"] for line in skip] == ["WARNING"]
    assert "is not tagged" not in skip[0]["message"]


def test_git_trouble_names_git_s_own_reason(monkeypatch, tmp_path, capsys):
    """The last stderr line is what an operator needs; the exit code alone is not."""
    git = _git(returncode=128, stderr="warning: x\nfatal: Could not resolve host: github.com\n")
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=git) == 0
    out = capsys.readouterr().out
    assert "git ls-remote exited 128 for v1.2.3" in out
    assert "Could not resolve host: github.com" in out


def test_git_trouble_does_not_hide_schema_drift(monkeypatch, tmp_path):
    live = {**_COMMITTED, "openapi": "3.0.0"}
    git = _git_raises(subprocess.TimeoutExpired("git", 10))
    assert _run(monkeypatch, tmp_path, _opener(live), git=git) == 3


@pytest.mark.parametrize(
    "stdout",
    [
        "<html>unicorn</html>\n",
        f"not-a-sha\t{_REF}\n",
        f"{_SHA}\t{_REF}^{{}}\n",
    ],
    ids=["garbage", "bad-sha", "peeled-without-tag"],
)
def test_unparseable_ls_remote_output_skips_the_tag_check(monkeypatch, tmp_path, capsys, stdout):
    """git answered 0 with something it never prints: not a finding, never a crash."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=_git(stdout)) == 0
    out = capsys.readouterr().out
    assert "release tag not checked" in out
    assert "is not tagged" not in out


def test_ls_remote_globs_match_only_the_exact_tag(monkeypatch, tmp_path, capsys):
    """Patterns are globs matched on the tail: ``v1.2*`` also returns v1.2.3. Absent."""
    live = {**_COMMITTED, "info": {"title": "power-map", "version": "1.2*"}}
    git = _git(_lightweight() + _annotated(ref="refs/tags/v1.2.30"))
    assert _run(monkeypatch, tmp_path, _opener(live), committed=live, git=git) == 4
    assert "v1.2* is not tagged" in capsys.readouterr().out


def test_a_sha256_repository_s_object_names_are_accepted(monkeypatch, tmp_path, capsys):
    sha = "c" * 64
    github = {_raw_url(sha): _COMMITTED}
    assert (
        _run(monkeypatch, tmp_path, _opener(_COMMITTED, github), git=_git(_lightweight(sha))) == 0
    )
    assert f"at {sha[:12]} matches" in capsys.readouterr().out


def test_git_is_asked_over_https_for_the_tag_and_its_peel(monkeypatch, tmp_path):
    """Not ``origin``: in the main checkout that is SSH, which needs the deploy key."""
    calls = []
    assert (
        _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=_git(_lightweight(), calls=calls)) == 0
    )
    [(cmd, kwargs)] = calls
    assert cmd == [
        "git",
        "ls-remote",
        "--exit-code",
        "https://github.com/CannObserv/power-map",
        "refs/tags/v1.2.3",
        "refs/tags/v1.2.3^{}",
    ]
    assert GIT_URL == "https://github.com/CannObserv/power-map"
    assert kwargs["timeout"] == 10.0
    assert kwargs["cwd"] == "/"
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_git_runs_with_a_scrubbed_non_interactive_environment(monkeypatch):
    """A hook's GIT_DIR, a credential prompt, or an insteadOf rewrite would each derail it."""
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GIT_INDEX_FILE", "/elsewhere/index")
    monkeypatch.setenv(
        "GIT_CONFIG_PARAMETERS", "'url.git@github.com:.insteadof'='https://github.com/'"
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = git_env()
    assert "GIT_DIR" not in env
    assert "GIT_INDEX_FILE" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["PATH"] == "/usr/bin:/bin"


def test_the_git_seam_gets_that_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    calls = []
    _run(monkeypatch, tmp_path, _opener(_COMMITTED), git=_git(_lightweight(), calls=calls))
    [(_, kwargs)] = calls
    assert kwargs["env"] == git_env()
    assert "GIT_DIR" not in kwargs["env"]


def test_a_served_schema_without_a_version_skips_the_tag_check(monkeypatch, tmp_path, capsys):
    unversioned = {k: v for k, v in _COMMITTED.items() if k != "info"}

    def opener(url, timeout):
        assert url == _LIVE_URL, "no version, so GitHub is never asked"
        return io.BytesIO(json.dumps(unversioned).encode())

    assert _run(monkeypatch, tmp_path, opener, committed=unversioned, git=_never_git) == 0
    assert "no info.version" in capsys.readouterr().out


def test_no_tag_check_never_asks_github(monkeypatch, tmp_path):
    """For a dev server on 8001, whose version is often bumped and untagged."""

    def opener(url, timeout):
        assert url == _LIVE_URL
        return io.BytesIO(json.dumps(_COMMITTED).encode())

    assert _run(monkeypatch, tmp_path, opener, extra=["--no-tag-check"], git=_never_git) == 0


def test_a_tagged_snapshot_that_is_not_json_is_a_mis_tag(monkeypatch, tmp_path, capsys):
    """GitHub served the file; its content is wrong, so the tag is the finding."""
    raw = _raw_url()

    def opener(url, timeout):
        if url == raw:
            return io.BytesIO(b"<<<<<<< conflict")
        return _opener(_COMMITTED)(url, timeout)

    assert _run(monkeypatch, tmp_path, opener) == 4
    assert "differs from the served schema" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("reset"),
        _http_error(_raw_url(), 503),
        _http_error(_raw_url(), 429),
        TimeoutError("timed out"),
        http.client.IncompleteRead(b"", 10),
    ],
    ids=["unreachable", "unavailable", "rate-limited", "timeout", "truncated"],
)
def test_trouble_fetching_the_tagged_snapshot_skips_the_check(
    monkeypatch, tmp_path, capsys, failure
):
    """Only a 404 there means the tagged commit lacks the snapshot. urllib wraps OSError
    only around sending, so a broken response arrives unwrapped."""
    assert _run(monkeypatch, tmp_path, _opener(_COMMITTED, {_raw_url(): failure})) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    skip = [line for line in lines if "release tag not checked" in line["message"]]
    assert [line["level"] for line in skip] == ["WARNING"]


def test_a_protocol_error_on_the_tagged_snapshot_does_not_hide_drift(monkeypatch, tmp_path):
    live = {**_COMMITTED, "openapi": "3.0.0"}
    github = {_raw_url(): http.client.IncompleteRead(b"", 10)}
    assert _run(monkeypatch, tmp_path, _opener(live, github)) == 3


def test_a_protocol_error_on_the_live_schema_is_a_fetch_failure(monkeypatch, tmp_path, capsys):
    def opener(url, timeout):
        raise http.client.IncompleteRead(b"{", 100)

    assert _run(monkeypatch, tmp_path, opener, git=_never_git) == 1
    assert "could not fetch" in capsys.readouterr().out


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


# --- the real git binary, against a local bare repo (#824) ---------------------
#
# The seam tests above pin the argv and the parser to what git prints today.
# These run that argv through the real binary, so a git that prints the peel or
# the absent case differently fails here, not in production. file:// only: never
# the network.


def _real_git(*args: str, cwd: Path) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    result = subprocess.run(cmd, cwd=cwd, env=git_env(), capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def bare_repo(tmp_path) -> tuple[str, str]:
    """A file:// bare repo with an annotated v1.0.0 and a lightweight v1.0.1 on one commit."""
    work = tmp_path / "work"
    work.mkdir()
    _real_git("init", "-q", cwd=work)
    _real_git("commit", "-q", "--allow-empty", "-m", "c", cwd=work)
    commit = _real_git("rev-parse", "HEAD", cwd=work)
    _real_git("tag", "-a", "v1.0.0", "-m", "v1.0.0", cwd=work)
    _real_git("tag", "v1.0.1", cwd=work)
    _real_git("tag", "v1.0.10", cwd=work)
    bare = tmp_path / "bare.git"
    _real_git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return bare.as_uri(), commit


@pytest.mark.parametrize("tag", ["v1.0.0", "v1.0.1"], ids=["annotated", "lightweight"])
def test_real_git_resolves_a_tag_to_its_commit(bare_repo, tag):
    url, commit = bare_repo
    assert tagged_commit(tag, 10.0, subprocess.run, url=url) == commit


def test_real_git_reports_an_absent_tag_as_none(bare_repo):
    url, _ = bare_repo
    assert tagged_commit("v9.9.9", 10.0, subprocess.run, url=url) is None


def test_real_git_matches_only_the_exact_tag(bare_repo):
    """``v1.0.1*`` would glob v1.0.1 and v1.0.10; neither is the tag asked for."""
    url, _ = bare_repo
    assert tagged_commit("v1.0.1*", 10.0, subprocess.run, url=url) is None
