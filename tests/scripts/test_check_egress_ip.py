"""Tests for scripts.check_egress_ip — egress-IP drift guard (#410).

On 2026-08-09 the VM's NAT egress IP rotated 67.213.124.9 -> 69.67.149.183
with no VM-side change. The DO cluster gates on source IP, so every DB-backed
route died at once and stayed dead for ~35 minutes.

This guard answers one question: *is the address we leave this box from still
one the database allowlist knows about?* Three properties matter, and each has
a test below:

1. The current IP appears in the alert. Triage ends with pasting it into
   Trusted Sources, so an alert that omits it costs a lookup.
2. A junk response from an echo service is not an IP. Some return an HTML
   error page with 200, which would otherwise "drift" us every run.
3. Failing to determine the IP is not drift. A third-party outage must not
   open an alert about our network.
"""

import json
import logging
import sys

import pytest

from scripts.check_egress_ip import (
    DEFAULT_SERVICES,
    EGRESS_ALERT,
    lookup,
    main,
    parse_expected,
)


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


def _run(monkeypatch, *, body=b"69.67.149.183", argv=("--no-gh",), env=None):
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", *argv])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(body))
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return main


def test_main_exits_clean_when_the_ip_matches(monkeypatch):
    run = _run(monkeypatch, argv=("--no-gh", "--expected", "69.67.149.183"))
    run()  # no SystemExit


def test_main_exits_3_on_drift(monkeypatch):
    run = _run(monkeypatch, argv=("--no-gh", "--expected", "67.213.124.9"))
    with pytest.raises(SystemExit) as excinfo:
        run()
    assert excinfo.value.code == 3


def test_main_drift_warning_names_both_addresses(monkeypatch, capsys):
    """Triage is 'paste this into Trusted Sources' — the alert must carry it."""
    run = _run(monkeypatch, argv=("--no-gh", "--expected", "67.213.124.9"))
    with pytest.raises(SystemExit):
        run()
    out = capsys.readouterr().out
    assert "69.67.149.183" in out
    assert "67.213.124.9" in out


def test_main_accepts_any_of_several_expected(monkeypatch):
    run = _run(monkeypatch, argv=("--no-gh", "--expected", "67.213.124.9,69.67.149.183"))
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
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh", "--expected", "69.67.149.183"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(OSError("down")))
    main()  # no SystemExit
    assert "could not determine" in capsys.readouterr().out.lower()


def test_main_no_gh_hatch_suppresses_surfacing(monkeypatch):
    called = []
    monkeypatch.setenv("EGRESS_CHECK_NO_GH", "1")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", "67.213.124.9"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts._alerting.gh", lambda args: called.append(args) or (0, ""))
    with pytest.raises(SystemExit):
        main()
    assert called == []


def test_main_surfaces_drift_with_the_current_ip_in_the_body(monkeypatch):
    """The GitHub alert must be actionable without opening a shell on the VM."""
    calls = []
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--expected", "67.213.124.9"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr(
        "scripts._alerting.gh",
        lambda args: (calls.append(args), (0, "") if "list" not in args else (0, ""))[1],
    )
    with pytest.raises(SystemExit):
        main()
    # "create" alone also matches the label-create call, which carries no body.
    create = next((c for c in calls if "issue" in c and "create" in c), None)
    assert create is not None
    body = create[create.index("--body") + 1]
    assert "69.67.149.183" in body


def test_main_force_fail_hatch(monkeypatch):
    monkeypatch.setenv("EGRESS_CHECK_FORCE_FAIL", "1")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_alert_identity_is_distinct_from_the_readiness_guard(monkeypatch):
    """Separate labels: two causes, two issues, independent open/close cycles."""
    from scripts.check_ready import READY_ALERT

    assert EGRESS_ALERT.label != READY_ALERT.label
    assert EGRESS_ALERT.unit != READY_ALERT.unit


def test_main_logs_at_warning_on_drift(monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh", "--expected", "67.213.124.9"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit):
        main()


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
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
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
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: ["1.2.3.4"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_api_catches_a_rule_being_removed(monkeypatch):
    """The failure the env var cannot see: our IP deleted from Trusted Sources."""
    monkeypatch.setenv("DO_API_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
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
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
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
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
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
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    monkeypatch.setattr("scripts.check_egress_ip.fetch_allowed_ips", lambda *a, **k: [])
    main()  # no SystemExit
    assert "no ip restrictions" in capsys.readouterr().out.lower()


def test_no_token_still_uses_the_env_var(monkeypatch, capsys):
    """Fallback path stays intact for any host without a DO token."""
    monkeypatch.setenv("EGRESS_EXPECTED_IPS", "67.213.124.9")
    monkeypatch.setattr(sys, "argv", ["check_egress_ip", "--no-gh"])
    monkeypatch.setattr("scripts.check_egress_ip.urlopen", _opener(b"69.67.149.183"))
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3
    assert "EGRESS_EXPECTED_IPS" in capsys.readouterr().out
