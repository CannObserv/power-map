"""Unit tests for the roles list-merge filter parser (#251).

The roles list is cross-org with a two-value status axis (`active` /
`archived`) plus a second free-text filter `org_q` (organization name) that
neither People nor Orgs has. `_parse_roles_list_filters` re-derives all of
these from HX-Current-URL so a list-flow role merge can re-render the roles
list region under the user's active filters.
"""

from types import SimpleNamespace

from src.api.admin.orgs_roles import _parse_roles_list_filters


def _req(hx_current_url: str | None = None):
    """Build the minimal Request shape the parser needs: just `.headers.get`."""
    headers = {} if hx_current_url is None else {"HX-Current-URL": hx_current_url}
    return SimpleNamespace(headers=headers)


def test_missing_header_returns_defaults():
    assert _parse_roles_list_filters(_req()) == {
        "q": "",
        "org_q": "",
        "status": "active",
        "page": 1,
        "page_size": 50,
    }


def test_parses_q():
    out = _parse_roles_list_filters(_req("/admin/roles/?q=Director"))
    assert out["q"] == "Director"


def test_parses_org_q():
    out = _parse_roles_list_filters(_req("/admin/roles/?org_q=Acme"))
    assert out["org_q"] == "Acme"


def test_strips_whitespace_from_org_q():
    out = _parse_roles_list_filters(_req("/admin/roles/?org_q=%20%20Acme%20%20"))
    assert out["org_q"] == "Acme"


def test_parses_status_active():
    out = _parse_roles_list_filters(_req("/admin/roles/?status=active"))
    assert out["status"] == "active"


def test_parses_status_archived():
    out = _parse_roles_list_filters(_req("/admin/roles/?status=archived"))
    assert out["status"] == "archived"


def test_inactive_is_not_a_roles_status_falls_back_to_active():
    """Roles have no `active` flag (org-only), so `inactive` is invalid here."""
    out = _parse_roles_list_filters(_req("/admin/roles/?status=inactive"))
    assert out["status"] == "active"


def test_unknown_status_falls_back_to_active():
    out = _parse_roles_list_filters(_req("/admin/roles/?status=banana"))
    assert out["status"] == "active"


def test_parses_page():
    out = _parse_roles_list_filters(_req("/admin/roles/?page=3"))
    assert out["page"] == 3


def test_garbage_page_falls_back_to_one():
    out = _parse_roles_list_filters(_req("/admin/roles/?page=abc"))
    assert out["page"] == 1


def test_parses_page_size():
    out = _parse_roles_list_filters(_req("/admin/roles/?page_size=25"))
    assert out["page_size"] == 25


def test_page_size_out_of_bounds_falls_back_to_default():
    assert _parse_roles_list_filters(_req("/admin/roles/?page_size=9999"))["page_size"] == 50
    assert _parse_roles_list_filters(_req("/admin/roles/?page_size=5"))["page_size"] == 50


def test_parses_all_at_once():
    out = _parse_roles_list_filters(
        _req("/admin/roles/?q=Dir&org_q=Acme&status=archived&page=2&page_size=100"),
    )
    assert out == {
        "q": "Dir",
        "org_q": "Acme",
        "status": "archived",
        "page": 2,
        "page_size": 100,
    }


def test_extra_unknown_params_ignored():
    out = _parse_roles_list_filters(_req("/admin/roles/?q=Dir&utm_source=test"))
    assert out["q"] == "Dir"
    assert out["org_q"] == ""
