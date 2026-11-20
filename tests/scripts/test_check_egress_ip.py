"""Tests for scripts.check_egress_ip — egress-IP drift guard (#410).

On 2026-08-09 the VM's NAT egress IP rotated 67.213.124.9 -> 69.67.149.183
with no VM-side change. The DO cluster gates on source IP, so every DB-backed
route died at once and stayed dead for ~35 minutes.

This guard answers one question: *is the address we leave this box from still
one the database allowlist knows about?* Three properties matter, and each has
a test below:

1. The current IP appears in the journal WARNING. Triage ends with pasting it
   into Trusted Sources; the alert check-in (#566) points at that journal.
2. A junk response from an echo service is not an IP. Some return an HTML
   error page with 200, which would otherwise "drift" us every run.
3. Failing to determine the IP is not drift. A third-party outage must not
   open an alert about our network.
"""

import json
import re
import sys
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from scripts.check_egress_ip import (
    API_ATTEMPTS,
    API_RETRY_DELAYS,
    DEFAULT_SERVICES,
    DEFAULT_TIMEOUT,
    EXIT_UNREADABLE,
    fetch_with_retry,
    lookup,
    main,
    parse_expected,
)
from scripts.status_checkin import parse_exit_kinds


@pytest.fixture(autouse=True)
def slept(monkeypatch):
    """No test waits out a real retry back-off; each records what it would have."""
    delays: list[float] = []
    monkeypatch.setattr("scripts.check_egress_ip.sleep", delays.append)
    return delays


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _opener(*outcomes):
    """Opener yielding each outcome in turn; records the URLs asked."""
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)

    opener.calls = calls
    return opener


SERVICES = ("https://first.example", "https://second.example")


# --- lookup ----------------------------------------------------------------


def test_lookup_returns_the_first_answer():
    opener = lookup(SERVICES, timeout=1, opener=_opener(b"69.67.149.183"))
    assert opener == "69.67.149.183"


def test_lookup_strips_whitespace():
    """Echo services vary on trailing newlines."""
    assert lookup(SERVICES, timeout=1, opener=_opener(b"  69.67.149.183\n")) == "69.67.149.183"


def test_lookup_falls_through_when_a_service_errors():
    opener = _opener(OSError("connection reset"), b"69.67.149.183")
    assert lookup(SERVICES, timeout=1, opener=opener) == "69.67.149.183"
    assert len(opener.calls) == 2


def test_lookup_rejects_a_non_ip_body_and_tries_the_next():
    """A 200 carrying an HTML error page would otherwise read as a new IP."""
    opener = _opener(b"<html>error</html>", b"69.67.149.183")
    assert lookup(SERVICES, timeout=1, opener=opener) == "69.67.149.183"
    assert len(opener.calls) == 2


def test_lookup_returns_none_when_every_service_fails():
    opener = _opener(OSError("down"))
    assert lookup(SERVICES, timeout=1, opener=opener) is None


def test_lookup_accepts_ipv6():
    assert lookup(SERVICES, timeout=1, opener=_opener(b"2001:db8::1")) == "2001:db8::1"


def test_default_services_are_independent_providers():
    """One provider's outage must not blind the guard."""
    assert len(DEFAULT_SERVICES) >= 2
    hosts = {s.split("/")[2] for s in DEFAULT_SERVICES}
    assert len(hosts) == len(DEFAULT_SERVICES)


# --- parse_expected --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("69.67.149.183", {"69.67.149.183"}),
        ("69.67.149.183,67.213.124.9", {"69.67.149.183", "67.213.124.9"}),
        ("69.67.149.183, 67.213.124.9", {"69.67.149.183", "67.213.124.9"}),
        ("  ", set()),
        ("", set()),
    ],
)
def test_parse_expected(raw, expected):
    assert parse_expected(raw) == expected


# --- main ------------------------------------------------------------------


def _run(monkeypatch, *, body=b"69.67.149.183", argv=(), env=None):
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", *argv])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(body))
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return main


def test_main_exits_clean_when_the_ip_matches(monkeypatch):
    run = _run(monkeypatch, argv=("--expected", "69.67.149.183"))
    run()  # no SystemExit


def test_main_exits_3_on_drift(monkeypatch):
    run = _run(monkeypatch, argv=("--expected", "67.213.124.9"))
    with pytest.raises(SystemExit) as excinfo:
        run()
    assert excinfo.value.code == 3


def test_main_drift_warning_names_both_addresses(monkeypatch, capsys):
    """Triage is 'paste this into Trusted Sources' — the alert must carry it."""
    run = _run(monkeypatch, argv=("--expected", "67.213.124.9"))
    with pytest.raises(SystemExit):
        run()
    out = capsys.readouterr().out
    assert "69.67.149.183" in out
    assert "67.213.124.9" in out


def test_main_accepts_any_of_several_expected(monkeypatch):
    run = _run(monkeypatch, argv=("--expected", "67.213.124.9,69.67.149.183"))
    run()  # no SystemExit


def test_main_reads_expected_from_the_environment(monkeypatch):
    """Configured in /etc/power-map/.env alongside the DB credentials."""
    run = _run(monkeypatch, env={"EGRESS_EXPECTED_IPS": "69.67.149.183"})
    run()  # no SystemExit


def test_main_unconfigured_reports_the_ip_and_exits_clean(monkeypatch, capsys):
    """With nothing to compare against, say what to configure — do not alert."""
    monkeypatch.delenv("EGRESS_EXPECTED_IPS", raising=False)
    run = _run(monkeypatch)
    run()  # no SystemExit
    out = capsys.readouterr().out
    assert "69.67.149.183" in out
    assert "EGRESS_EXPECTED_IPS" in out


def test_main_undeterminable_ip_is_not_drift(monkeypatch, capsys):
    """A third-party echo outage must not raise an alert about our network."""
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", "69.67.149.183"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(OSError("down")))
    main()  # no SystemExit
    assert "could not determine" in capsys.readouterr().out.lower()


def test_main_force_fail_hatch(monkeypatch):
    monkeypatch.setenv("EGRESS_CHECK_FORCE_FAIL", "1")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_main_logs_the_fix_at_warning_on_drift(monkeypatch, capsys):
    """The journal is where the new address lives since #566: the alert check-in's
    body names ``journalctl``, never the address itself."""
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", "67.213.124.9"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with pytest.raises(SystemExit):
        main()
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    fix = [ln for ln in lines if "Trusted Sources" in ln["message"]]
    assert fix and fix[0]["level"] == "WARNING"
    assert "69.67.149.183" in fix[0]["message"]


def test_main_never_touches_github(monkeypatch):
    """#566: the co-status check-in replaced the ``egress-ip-drift`` issue."""

    # Recorded, not raised: the retired surfacing swallowed every exception.
    ran = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", "67.213.124.9"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3
    assert ran == []


# --- allowlist source: the DO API is authoritative (#409 unblocked this) -----


@pytest.fixture(autouse=True)
def _no_ambient_do_token(monkeypatch):
    """The VM's real token must not leak into tests that exercise the fallback."""
    monkeypatch.delenv("DO_API_TOKEN", raising=False)


def _do_api(ips, *, fail=False):
    """Fake DO API opener: cluster list, then a firewall payload."""
    calls = []

    def opener(request, timeout=None):
        calls.append(getattr(request, "full_url", request))
        if fail:
            raise OSError("DO API unreachable")
        payload = (
            {"databases": [{"id": "cid-1", "name": "co-pm-db-1"}]}
            if len(calls) == 1
            else {"rules": [{"type": "ip_addr", "value": ip} for ip in ips]}
        )
        return _Response(json.dumps(payload).encode())

    opener.calls = calls
    return opener


def test_allowlist_comes_from_the_do_api_when_a_token_is_present(monkeypatch, capsys):
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr(
        "scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: ["69.67.149.183"]
    )
    main()  # no SystemExit — it is in the live allowlist
    assert "DO API" in capsys.readouterr().out


def test_do_api_allowlist_drives_the_drift_verdict(monkeypatch):
    """The env var is not consulted at all when the API answers."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "69.67.149.183")  # would say "fine"
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: ["1.2.3.4"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_api_catches_a_rule_being_removed(monkeypatch):
    """The failure the env var cannot see: our IP deleted from Trusted Sources."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr(
        "scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: ["67.213.124.9"]
    )
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_api_failure_falls_back_to_the_env_var(monkeypatch, capsys):
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "69.67.149.183")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))

    def boom(*a, **k):
        raise OSError("DO API unreachable")

    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", boom)
    main()  # no SystemExit — fell back and matched
    assert "EGRESS_EXPECTED_IPS" in capsys.readouterr().out


def test_api_failure_without_a_fallback_is_not_drift(monkeypatch, capsys):
    """Someone else's outage must not alert about our network."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.delenv("EGRESS_EXPECTED_IPS", raising=False)
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))

    def boom(*a, **k):
        raise OSError("DO API unreachable")

    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", boom)
    main()  # no SystemExit
    assert "could not determine the allowlist" in capsys.readouterr().out.lower()


def test_empty_trusted_sources_means_unrestricted_not_drift(monkeypatch, capsys):
    """DO treats an empty Trusted Sources list as no IP restriction at all.

    Reporting drift there would be exactly backwards — nothing is being blocked.
    """
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.delenv("EGRESS_EXPECTED_IPS", raising=False)
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: [])
    main()  # no SystemExit
    assert "no ip restrictions" in capsys.readouterr().out.lower()


def test_no_token_still_uses_the_env_var(monkeypatch, capsys):
    """Fallback path stays intact for any host without a DO token."""
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "67.213.124.9")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3
    assert "EGRESS_EXPECTED_IPS" in capsys.readouterr().out


# --- CR1 findings 2 and 4: explicit flag wins; timeout is threaded -----------


def test_an_explicit_expected_flag_beats_the_api(monkeypatch):
    """CR1 finding 2: --expected silently lost to the API, so the flag lied.

    An operator passing an allowlist explicitly is debugging; honour it.
    """
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", "1.2.3.4"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr(
        "scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: ["69.67.149.183"]
    )
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3  # drift against the explicit set, not the API


def test_the_env_var_still_loses_to_the_api(monkeypatch, capsys):
    """Only an explicitly-passed flag wins — the env var stays a fallback."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "1.2.3.4")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr(
        "scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: ["69.67.149.183"]
    )
    main()  # no SystemExit — the API says it is fine
    assert "DO API" in capsys.readouterr().out


def test_timeout_is_threaded_into_the_do_api_call(monkeypatch):
    """CR1 finding 4: --timeout covered only the egress lookup."""
    seen = {}

    def recording(token, cluster, *, timeout=None, **kw):
        seen["timeout"] = timeout
        return ["69.67.149.183"]

    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--timeout", "3"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", recording)
    main()
    assert seen["timeout"] == 3


def test_an_explicit_but_empty_expected_is_unknown_not_drift(monkeypatch, capsys):
    """`--expected ""` asks to compare against nothing; that is not a verdict."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", ""])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    main()  # no SystemExit
    assert "could not determine the allowlist" in capsys.readouterr().out.lower()


def test_an_explicit_expected_of_only_separators_is_undetermined(monkeypatch, capsys):
    """CR2 finding 13: the label and the emptiness check must not disagree."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", ","])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    main()  # no SystemExit
    assert "could not determine the allowlist" in capsys.readouterr().out.lower()


def test_undetermined_message_does_not_misattribute_the_cause(monkeypatch, capsys):
    """With a token present, blaming a missing token sends triage the wrong way."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", ","])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    main()
    out = capsys.readouterr().out
    assert "could not determine the allowlist" in out.lower()
    assert "no DO_API_TOKEN" not in out


def test_resolve_allowlist_defaults_to_the_do_api_timeout():
    """CR3 finding 17: two same-named constants held different values."""
    import inspect

    from scripts import _do_api
    from scripts.check_egress_ip import resolve_allowlist

    default = inspect.signature(resolve_allowlist).parameters["timeout"].default
    assert default == _do_api.DEFAULT_TIMEOUT


TRIAGE = Path(__file__).parents[2] / "docs" / "RUNBOOK_DB_TRIAGE.md"


def _triage_section(heading: str) -> str:
    """Body of one ``###`` section of the DB triage runbook."""
    text = TRIAGE.read_text()
    start = text.index(f"### {heading}\n")
    end = text.find("\n### ", start + 1)
    return text[start : end if end != -1 else None]


def test_runbook_fix_keeps_the_fallback_allowlist_in_sync():
    """#839: a stale EGRESS_EXPECTED_IPS turned a DO API timeout into a false drift alert.

    The fallback only matters when the API is down, so a rotation that skips it
    goes unnoticed until then. The rotation checklist must name it.
    """
    fix = _triage_section("Fix")
    assert "EGRESS_EXPECTED_IPS" in fix
    assert "/etc/power-map/.env" in fix
    assert "Nothing to update for the guard" not in fix


def test_runbook_documents_the_manual_egress_lookup():
    """#839: the manual lookup, and how to tell a fallback-sourced false alarm."""
    section = _triage_section("Look up the egress IP by hand")
    assert "curl -s https://api.ipify.org" in section
    assert "journalctl -u power-map-egress-ip" in section
    assert "falling back" in section


# --- #839: retry the DO API; a fallback verdict is not drift ----------------


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://api.digitalocean.com/v2/databases", code, "x", Message(), None)


def _flaky(*outcomes):
    """fetch_allowed_ips stand-in: raise or return each outcome in turn."""
    calls = []

    def fetch(token, cluster, **kw):
        calls.append(kw)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    fetch.calls = calls
    return fetch


@pytest.mark.parametrize(
    "transient",
    [
        TimeoutError("The read operation timed out"),
        URLError("reset"),
        _http_error(503),
        _http_error(429),
        ConnectionResetError(),
    ],
    ids=["timeout", "urlerror", "503", "429", "reset"],
)
def test_a_transient_api_failure_is_retried(monkeypatch, slept, transient):
    """2026-11-19: 17 one-shot timeouts in a row each fell straight back to a stale list."""
    fetch = _flaky(transient, ["69.67.149.183"])
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", fetch)
    assert fetch_with_retry("tok", "co-pm-db-1", timeout=5) == ["69.67.149.183"]
    assert len(fetch.calls) == 2
    assert slept == [API_RETRY_DELAYS[0]]


def test_retries_stop_after_the_last_attempt(monkeypatch, slept):
    fetch = _flaky(TimeoutError("timed out"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", fetch)
    with pytest.raises(TimeoutError):
        fetch_with_retry("tok", "co-pm-db-1", timeout=5)
    assert len(fetch.calls) == API_ATTEMPTS
    assert slept == list(API_RETRY_DELAYS)


@pytest.mark.parametrize(
    "permanent",
    [
        _http_error(401),
        _http_error(403),
        _http_error(404),
        LookupError("no cluster"),
        ValueError("off-host cursor"),
    ],
    ids=["401", "403", "404", "no-cluster", "off-host"],
)
def test_a_permanent_api_failure_is_not_retried(monkeypatch, slept, permanent):
    """A bad token or a missing cluster answers the same way every time."""
    fetch = _flaky(permanent)
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", fetch)
    with pytest.raises(type(permanent)):
        fetch_with_retry("tok", "co-pm-db-1", timeout=5)
    assert len(fetch.calls) == 1
    assert slept == []


def test_the_timeout_reaches_every_attempt(monkeypatch):
    fetch = _flaky(TimeoutError(), ["69.67.149.183"])
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", fetch)
    fetch_with_retry("tok", "co-pm-db-1", timeout=7)
    assert [c["timeout"] for c in fetch.calls] == [7, 7]


def test_main_recovers_from_one_api_timeout_without_touching_the_fallback(monkeypatch, capsys):
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "1.2.3.4")  # stale: would mismatch
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr(
        "scripts.check_egress_ip.fetch_allowed_ips", _flaky(TimeoutError(), ["69.67.149.183"])
    )
    main()  # no SystemExit
    out = capsys.readouterr().out
    assert "source: the DO API" in out
    assert "falling back" not in out


def test_a_fallback_mismatch_after_an_api_failure_is_not_drift(monkeypatch, capsys):
    """#839: the fallback is a hand-kept copy; when it disagrees, it is the suspect.

    The 2026-11-19 false alarm, replayed: the API is down, the fallback still
    names the previous address, the real one is allowlisted.
    """
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "69.67.149.183")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"103.219.170.149"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", _flaky(TimeoutError()))
    main()  # no SystemExit: not drift
    out = capsys.readouterr().out
    assert "DRIFT" not in out
    assert "cannot verify" in out.lower()
    assert "103.219.170.149" in out and "69.67.149.183" in out
    assert "EGRESS_EXPECTED_IPS" in out


# --- a permanent API failure is unreadable, in the same run (#833 Q3) ------
#
# #841 left a revoked token quiet: the fallback answered, the guard went blind,
# and nothing said so. Judged by cause, not by a run count: a refusal answers
# the same way every run, so it alerts now; a blip stays quiet (the next tick
# retries, and /ready catches a real cut-off).


def _refused_run(monkeypatch, *, fallback: str | None, current: bytes, error: Exception):
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    if fallback is None:
        monkeypatch.delenv("EGRESS_EXPECTED_IPS", raising=False)
    else:
        monkeypatch.setenv("EGRESS_EXPECTED_IPS", fallback)
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(current))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", _flaky(error))
    with pytest.raises(SystemExit) as excinfo:
        main()
    return excinfo.value.code


@pytest.mark.parametrize(
    "error",
    [
        _http_error(401),
        _http_error(403),
        _http_error(404),
        LookupError("no cluster"),
        ValueError("refusing to follow a pagination cursor off-host"),
    ],
    ids=["401", "403", "404", "no-cluster", "bad-body"],
)
def test_a_permanent_api_failure_is_unreadable(monkeypatch, error):
    assert _refused_run(monkeypatch, fallback=None, current=b"69.67.149.183", error=error) == (
        EXIT_UNREADABLE
    )


def test_unreadable_even_when_the_fallback_agrees(monkeypatch, capsys):
    """The case #841 passed as ok: the address looks fine, but the guard is blind."""
    code = _refused_run(
        monkeypatch, fallback="69.67.149.183", current=b"69.67.149.183", error=_http_error(401)
    )
    assert code == EXIT_UNREADABLE
    out = capsys.readouterr().out
    assert "UNREADABLE" in out and "DO_API_TOKEN" in out
    assert "lists it" in out


def test_a_permanent_api_failure_with_a_stale_fallback_is_unreadable_not_drift(monkeypatch, capsys):
    code = _refused_run(
        monkeypatch, fallback="69.67.149.183", current=b"103.219.170.149", error=_http_error(401)
    )
    assert code == EXIT_UNREADABLE
    out = capsys.readouterr().out
    assert "DRIFT" not in out
    assert "does not list it" in out


def test_a_spent_transient_retry_stays_quiet(monkeypatch, capsys):
    """Unchanged by #833: DO's own outage is not ours to page on (#841)."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "69.67.149.183")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", _flaky(_http_error(503)))
    main()  # no SystemExit
    assert "UNREADABLE" not in capsys.readouterr().out


def test_the_unit_names_the_unreadable_exit():
    unit = (Path(__file__).parents[2] / "infra" / "power-map-egress-ip.service").read_text()
    (raw,) = re.findall(r"^Environment=STATUS_EXIT_KINDS=(.*)$", unit, re.M)
    assert parse_exit_kinds(raw) == {"3": "drift", str(EXIT_UNREADABLE): "unreadable"}


def test_worst_case_run_fits_the_units_start_timeout():
    """Retries multiply the DO API's share of the run; systemd kills at TimeoutStartSec.

    Worst case: every echo service times out, then every attempt's two DO
    requests (the cluster list, then its firewall) do too, plus every back-off.
    A unit killed mid-run reports neither ok nor drift.
    """
    unit = (Path(__file__).parents[2] / "infra" / "power-map-egress-ip.service").read_text()
    limit = int(re.search(r"^TimeoutStartSec=(\d+)$", unit, re.M).group(1))
    requests_per_attempt = 2
    worst = (
        len(DEFAULT_SERVICES) * DEFAULT_TIMEOUT
        + API_ATTEMPTS * requests_per_attempt * DEFAULT_TIMEOUT
        + sum(API_RETRY_DELAYS)
    )
    headroom = 15  # uv start-up, imports, logging
    assert worst + headroom <= limit, (worst, limit)
    assert len(API_RETRY_DELAYS) == API_ATTEMPTS - 1
