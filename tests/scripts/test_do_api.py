"""Tests for scripts._do_api — read-only DigitalOcean API helpers (#409, #410).

Split out of test_write_terraform_credentials when check_egress_ip needed the
same authoritative answer: what does the database cluster's allowlist actually
contain right now?
"""

import json

import pytest

from scripts._do_api import API_ROOT, MAX_PAGES, _request, fetch_allowed_ips

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


# --- CR1 finding 3: /v2/databases is paginated ------------------------------


PAGE_1 = {
    "databases": [{"id": "cid-a", "name": "other-1"}],
    "links": {"pages": {"next": "https://api.digitalocean.com/v2/databases?page=2&per_page=200"}},
}
PAGE_2 = {"databases": [{"id": "cid-2", "name": "co-pm-db-1"}], "links": {"pages": {}}}


def test_follows_pagination_to_find_a_later_cluster():
    """DO pages at 20 by default; a cluster past page 1 used to raise LookupError."""
    opener = _api(PAGE_1, PAGE_2, FIREWALL)
    assert fetch_allowed_ips("token", "co-pm-db-1", opener=opener) == [
        "69.67.149.183",
        "67.213.124.9",
    ]
    assert "page=2" in opener.calls[1]


def test_requests_a_large_page_size():
    """Fewer round trips, and most accounts then need only one."""
    opener = _api(CLUSTERS, FIREWALL)
    fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    assert "per_page=" in opener.calls[0]


def test_stops_paging_once_the_cluster_is_found():
    """No reason to walk the rest of the account."""
    opener = _api(PAGE_2, FIREWALL)
    fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    assert len(opener.calls) == 2  # cluster page + firewall, no further paging


def test_unknown_cluster_reports_after_exhausting_pages():
    opener = _api(PAGE_1, PAGE_2, FIREWALL)
    with pytest.raises(LookupError):
        fetch_allowed_ips("token", "absent", opener=opener)


# --- CR2 findings 11 and 12: bound the walk, keep it on DO ------------------


def _endless_pages():
    """A cursor that never terminates — the hang CR2 finding 11 guards against."""
    calls = []

    def opener(request, timeout=None):
        calls.append(getattr(request, "full_url", request))
        return _Response(
            {
                "databases": [{"id": "x", "name": "not-the-one"}],
                "links": {"pages": {"next": f"{API_ROOT}/databases?page={len(calls) + 1}"}},
            }
        )

    opener.calls = calls
    return opener


def test_paging_is_bounded():
    """Unbounded, this spun until systemd killed the unit — every 5 minutes."""
    opener = _endless_pages()
    with pytest.raises(LookupError):
        fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    assert len(opener.calls) <= MAX_PAGES


def test_refuses_to_follow_a_cursor_off_digitalocean():
    """The bearer token rides on every request; it must not leave the host."""
    off_host = {
        "databases": [{"id": "x", "name": "other"}],
        "links": {"pages": {"next": "https://evil.example/v2/databases?page=2"}},
    }
    opener = _api(off_host, FIREWALL)
    with pytest.raises(ValueError) as excinfo:
        fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    assert "evil.example" in str(excinfo.value)
    assert len(opener.calls) == 1  # never fetched


def test_a_normal_next_cursor_is_still_followed():
    opener = _api(PAGE_1, PAGE_2, FIREWALL)
    assert fetch_allowed_ips("token", "co-pm-db-1", opener=opener)


# --- CR3 findings 15 and 16 -------------------------------------------------


def test_hitting_the_page_cap_is_not_reported_as_a_missing_cluster():
    """CR3 finding 15: the cap said "no such cluster", asserting absence.

    A mistyped --cluster and a 20-page account are different problems and must
    not produce the same confident error.
    """
    opener = _endless_pages()
    with pytest.raises(LookupError) as excinfo:
        fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    message = str(excinfo.value)
    assert str(MAX_PAGES) in message
    assert "no DigitalOcean database cluster named" not in message


def test_a_genuinely_absent_cluster_still_says_so():
    """The other half of 15 — the honest 'not found' must survive."""
    opener = _api(PAGE_1, PAGE_2, FIREWALL)
    with pytest.raises(LookupError) as excinfo:
        fetch_allowed_ips("token", "absent", opener=opener)
    assert "no DigitalOcean database cluster named" in str(excinfo.value)


def test_refuses_a_lookalike_host():
    """CR3 finding 16: prefix matching is safe only while API_ROOT keeps /v2.

    Compare the parsed hostname so an edit to API_ROOT cannot weaken this.
    """
    lookalike = {
        "databases": [{"id": "x", "name": "other"}],
        "links": {"pages": {"next": "https://api.digitalocean.com.evil.example/v2/databases"}},
    }
    opener = _api(lookalike, FIREWALL)
    with pytest.raises(ValueError) as excinfo:
        fetch_allowed_ips("token", "co-pm-db-1", opener=opener)
    assert "evil.example" in str(excinfo.value)
