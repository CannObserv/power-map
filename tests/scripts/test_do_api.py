"""Tests for scripts._do_api — read-only DigitalOcean API helpers (#409, #410).

Split out of test_write_terraform_credentials when check_egress_ip needed the
same authoritative answer: what does the database cluster's allowlist actually
contain right now?
"""

import json

import pytest

from scripts._do_api import _request, fetch_allowed_ips

# --- fetch_allowed_ips -----------------------------------------------------


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


CLUSTERS = {"databases": [{"id": "cid-1", "name": "co-pm-db-1"}, {"id": "cid-2", "name": "other"}]}
FIREWALL = {
    "rules": [
        {"type": "ip_addr", "value": "69.67.149.183"},
        {"type": "ip_addr", "value": "67.213.124.9"},
        {"type": "droplet", "value": "some-droplet-uuid"},
    ]
}


def _api(*payloads):
    calls = []

    def opener(request, timeout=None):
        calls.append(getattr(request, "full_url", request))
        return _Response(payloads[min(len(calls) - 1, len(payloads) - 1)])

    opener.calls = calls
    return opener


def test_fetch_allowed_ips_returns_the_ip_rules():
    opener = _api(CLUSTERS, FIREWALL)
    assert fetch_allowed_ips("token", "co-pm-db-1", opener=opener) == [
        "69.67.149.183",
        "67.213.124.9",
    ]


def test_fetch_allowed_ips_ignores_non_ip_rule_types():
    """A droplet or tag rule is not an address and must not reach tfvars."""
    opener = _api(CLUSTERS, FIREWALL)
    assert "some-droplet-uuid" not in fetch_allowed_ips("token", "co-pm-db-1", opener=opener)


def test_fetch_allowed_ips_targets_the_named_cluster():
    opener = _api(CLUSTERS, FIREWALL)
    fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    assert "cid-1" in opener.calls[1]


def test_fetch_allowed_ips_raises_on_unknown_cluster():
    opener = _api(CLUSTERS, FIREWALL)
    with pytest.raises(LookupError) as excinfo:
        fetch_allowed_ips("token", "nope", opener=opener)
    assert "nope" in str(excinfo.value)


def test_requests_carry_the_bearer_token():
    assert _request("https://x", "tok123").get_header("Authorization") == "Bearer tok123"
