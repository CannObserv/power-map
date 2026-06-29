"""Unit tests for the shared `parse_list_filters` helper (#250 CR round 1).

The People and Orgs wrappers each have their own exhaustive parser suites
(test_people_merge_filter_parser / test_orgs_merge_filter_parser); these pin
down the two parameters that differ between callers — `valid_statuses` and
`default_page_size` — at the shared-helper level.
"""

from types import SimpleNamespace

from src.api.admin.list_filters import parse_list_filters


def _req(hx_current_url: str | None = None):
    headers = {} if hx_current_url is None else {"HX-Current-URL": hx_current_url}
    return SimpleNamespace(headers=headers)


def test_status_in_valid_set_is_kept():
    out = parse_list_filters(
        _req("/admin/orgs/?status=inactive"),
        valid_statuses={"active", "inactive", "archived"},
    )
    assert out["status"] == "inactive"


def test_status_outside_valid_set_falls_back_to_active():
    """A status the caller doesn't allow collapses to active — this is the guard
    that stops People (two-valued) from leaking an org-only status."""
    out = parse_list_filters(
        _req("/admin/people/?status=inactive"),
        valid_statuses={"active", "archived"},
    )
    assert out["status"] == "active"


def test_default_page_size_used_when_absent():
    out = parse_list_filters(_req("/admin/orgs/"), valid_statuses={"active"}, default_page_size=25)
    assert out["page_size"] == 25


def test_default_page_size_is_50_when_unspecified():
    out = parse_list_filters(_req("/admin/orgs/"), valid_statuses={"active"})
    assert out["page_size"] == 50


def test_out_of_range_page_size_falls_back_to_caller_default():
    out = parse_list_filters(
        _req("/admin/orgs/?page_size=9999"), valid_statuses={"active"}, default_page_size=25
    )
    assert out["page_size"] == 25


def test_missing_header_returns_defaults_with_caller_page_size():
    out = parse_list_filters(_req(), valid_statuses={"active"}, default_page_size=100)
    assert out == {"q": "", "status": "active", "page": 1, "page_size": 100}


def test_malformed_url_returns_defaults():
    out = parse_list_filters(_req("::not a url::"), valid_statuses={"active"})
    assert out["status"] == "active"
    assert out["page"] == 1
