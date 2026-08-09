"""Tests for scripts.check_ready — the /ready uptime guard (#347).

The guard is HTTP-only: it never touches the database, so there is no DSN
layer here. What it *does* own is three things worth pinning down:

1. Turning a probe response into a reason slug (``pool_timeout`` and friends),
   because that slug is most of the triage — the 2026-08-09 outage read
   ``pool_timeout`` from first failure to fix, which said "connect-time
   failure", not "slow query".
2. Not crying wolf on a single blip — a failure followed by a success inside
   one run is green.
3. Not spamming. At a 2-minute cadence a comment per failing run would post
   ~30 comments an hour, so an already-open issue is left alone.

``urlopen`` raises ``HTTPError`` for 503 rather than returning a response, and
``HTTPError`` doubles as a readable response object. The fakes here reproduce
that shape so the tests fail the same way the real client would.
"""

import io
import logging
import sys
import urllib.error

import pytest

from scripts.check_ready import (
    DEFAULT_URL,
    READY_ALERT,
    ProbeResult,
    check,
    main,
    probe,
    summarize,
)

# --- fakes -----------------------------------------------------------------


class _FakeResponse:
    """Stand-in for the object ``urlopen`` returns on 2xx."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    """Build a real HTTPError — that is what urlopen raises for 503."""
    return urllib.error.HTTPError(DEFAULT_URL, status, "Service Unavailable", {}, io.BytesIO(body))


def _opener(*outcomes):
    """Opener returning/raising each outcome in turn; records call count."""
    calls = []

    def opener(url, timeout=None):
        calls.append((url, timeout))
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    opener.calls = calls
    return opener


def _ok_opener():
    return _opener(_FakeResponse(200, b'{"status":"ok"}'))


def _down_opener(reason=b"pool_timeout"):
    return _opener(_http_error(503, b'{"status":"unavailable","reason":"' + reason + b'"}'))


class _Runner:
    """Fake ``gh`` runner recording argv; returns queued (rc, stdout) pairs."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return self.results.pop(0) if self.results else (0, "")

    def ran(self, *needles):
        """True when some call contains all the given argv fragments."""
        return any(all(n in call for n in needles) for call in self.calls)


# --- probe: response -> reason slug ----------------------------------------


def test_probe_ok_on_200():
    result = probe(DEFAULT_URL, timeout=1, opener=_ok_opener())
    assert result.ok is True
    assert result.status == 200
    assert result.reason is None


def test_probe_extracts_reason_slug_from_503():
    """The slug is the triage. 2026-08-09 read pool_timeout throughout."""
    result = probe(DEFAULT_URL, timeout=1, opener=_down_opener())
    assert result.ok is False
    assert result.status == 503
    assert result.reason == "pool_timeout"


@pytest.mark.parametrize("slug", ["no_pool", "db_error", "pool_timeout"])
def test_probe_carries_every_documented_slug(slug):
    result = probe(DEFAULT_URL, timeout=1, opener=_down_opener(slug.encode()))
    assert result.reason == slug


def test_probe_unreachable_when_connection_refused():
    """Process down is a distinct failure from a 503 — the guard must say which."""
    opener = _opener(urllib.error.URLError("Connection refused"))
    result = probe(DEFAULT_URL, timeout=1, opener=opener)
    assert result.ok is False
    assert result.status is None
    assert result.reason == "unreachable"


def test_probe_labels_timeout():
    """A probe that outruns its own timeout is not the same as a clean 503."""
    result = probe(DEFAULT_URL, timeout=1, opener=_opener(TimeoutError()))
    assert result.ok is False
    assert result.reason == "probe_timeout"


def test_probe_labels_unexpected_status():
    """A proxy-layer 502 never reaches /ready's own vocabulary."""
    result = probe(DEFAULT_URL, timeout=1, opener=_opener(_http_error(502, b"<html>bad gateway")))
    assert result.ok is False
    assert result.status == 502
    assert result.reason == "http_502"


def test_probe_survives_malformed_body():
    """Unparseable body must not crash the guard — it degrades to a slug."""
    result = probe(DEFAULT_URL, timeout=1, opener=_opener(_http_error(503, b"not json")))
    assert result.ok is False
    assert result.reason == "unknown"


def test_probe_rejects_200_that_is_not_ok():
    """A 200 whose body does not say ok is not readiness."""
    opener = _opener(_FakeResponse(200, b'{"status":"unavailable"}'))
    assert probe(DEFAULT_URL, timeout=1, opener=opener).ok is False


def test_probe_passes_timeout_through():
    opener = _ok_opener()
    probe(DEFAULT_URL, timeout=7.5, opener=opener)
    assert opener.calls[0][1] == 7.5


# --- check: consecutive-failure gate ---------------------------------------


def test_check_stops_after_first_success():
    """A healthy service costs exactly one request per run."""
    opener = _ok_opener()
    results = check(
        DEFAULT_URL, attempts=2, retry_delay=0, timeout=1, opener=opener, sleep=lambda _: None
    )
    assert len(results) == 1
    assert results[-1].ok is True
    assert len(opener.calls) == 1


def test_check_blip_then_recovery_is_green():
    """One failed probe followed by a success is a blip, not an outage."""
    opener = _opener(
        _http_error(503, b'{"status":"unavailable","reason":"pool_timeout"}'),
        _FakeResponse(200, b'{"status":"ok"}'),
    )
    results = check(
        DEFAULT_URL, attempts=2, retry_delay=0, timeout=1, opener=opener, sleep=lambda _: None
    )
    assert results[-1].ok is True
    assert len(opener.calls) == 2


def test_check_fails_only_when_every_attempt_fails():
    opener = _down_opener()
    results = check(
        DEFAULT_URL, attempts=2, retry_delay=0, timeout=1, opener=opener, sleep=lambda _: None
    )
    assert len(results) == 2
    assert all(not r.ok for r in results)


def test_check_waits_between_attempts():
    """Back-to-back probes would both land inside the same blip."""
    slept = []
    check(
        DEFAULT_URL,
        attempts=2,
        retry_delay=11,
        timeout=1,
        opener=_down_opener(),
        sleep=slept.append,
    )
    assert slept == [11]


def test_check_does_not_sleep_after_the_last_attempt():
    """No dead time before exiting — the run ends on the final probe."""
    slept = []
    check(
        DEFAULT_URL,
        attempts=1,
        retry_delay=11,
        timeout=1,
        opener=_down_opener(),
        sleep=slept.append,
    )
    assert slept == []


# --- summarize -------------------------------------------------------------


def test_summary_carries_the_reason_slug():
    summary = summarize([ProbeResult(ok=False, status=503, reason="pool_timeout")])
    assert "pool_timeout" in summary
    assert "503" in summary


def test_summary_reports_attempt_count():
    results = [ProbeResult(ok=False, status=503, reason="pool_timeout")] * 2
    assert "2" in summarize(results)


# --- main ------------------------------------------------------------------


def test_main_exits_3_when_not_ready(monkeypatch):
    """Exit 3 marks the unit failed — same convention as the anomaly check."""
    monkeypatch.setattr(sys, "argv", ["check_ready", "--no-gh", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _down_opener())
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_main_exits_clean_when_ready(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_ready", "--no-gh", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _ok_opener())
    main()  # no SystemExit


def test_main_logs_the_reason_slug(monkeypatch, capsys):
    """The journal line is the first thing an operator reads."""
    monkeypatch.setattr(sys, "argv", ["check_ready", "--no-gh", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _down_opener())
    with pytest.raises(SystemExit):
        main()
    assert "pool_timeout" in capsys.readouterr().out


def test_main_no_gh_hatch_suppresses_surfacing(monkeypatch):
    """READY_CHECK_NO_GH is how the timer is exercised without touching GitHub."""
    called = []
    monkeypatch.setenv("READY_CHECK_NO_GH", "1")
    monkeypatch.setattr(sys, "argv", ["check_ready", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _down_opener())
    monkeypatch.setattr("scripts._alerting.gh", lambda args: called.append(args) or (0, ""))
    with pytest.raises(SystemExit):
        main()
    assert called == []


def test_main_force_fail_hatch(monkeypatch):
    """Exercise the surfacing path without breaking a real dependency."""
    monkeypatch.setenv("READY_CHECK_FORCE_FAIL", "1")
    monkeypatch.setattr(sys, "argv", ["check_ready", "--no-gh"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _ok_opener())
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_main_url_defaults_to_localhost_prod_port(monkeypatch):
    """The guard probes the local worker, not the public proxy (#347 option 1)."""
    assert DEFAULT_URL == "http://localhost:8000/ready"


def test_main_url_overridable_by_env(monkeypatch):
    opener = _ok_opener()
    monkeypatch.setenv("READY_PROBE_URL", "http://localhost:8001/ready")
    monkeypatch.setattr(sys, "argv", ["check_ready", "--no-gh", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", opener)
    main()
    assert opener.calls[0][0] == "http://localhost:8001/ready"


def test_main_default_timeout_exceeds_ready_worst_case(monkeypatch):
    """/ready bounds itself at ~4s (2s acquire + 2s query) — probe must outlast it."""
    opener = _ok_opener()
    monkeypatch.setattr(sys, "argv", ["check_ready", "--no-gh", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", opener)
    main()
    assert opener.calls[0][1] > 4


def test_main_surfaces_recovery_when_ready(monkeypatch):
    """Green runs still call gh — that is what closes a stale alert."""
    runner = _Runner((0, READY_ALERT.label), (0, ""))
    monkeypatch.setattr(sys, "argv", ["check_ready", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _ok_opener())
    monkeypatch.setattr("scripts._alerting.gh", runner)
    main()
    assert runner.calls


def test_main_gh_failure_does_not_mask_the_probe_result(monkeypatch, caplog):
    """Surfacing is best-effort; a broken gh must not turn an outage into exit 0."""

    def boom(args):
        raise OSError("gh exploded")

    monkeypatch.setattr(sys, "argv", ["check_ready", "--attempts", "1"])
    monkeypatch.setattr("scripts.check_ready.urlopen", _down_opener())
    monkeypatch.setattr("scripts._alerting.gh", boom)
    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3
