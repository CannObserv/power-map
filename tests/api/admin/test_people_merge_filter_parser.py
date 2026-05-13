"""Unit tests for `_parse_list_filters_from_hx_current_url`.

CR #4 follow-up: the parser handles `q`, `status`, `page`, and `page_size`,
but only `q` and `status` had integration coverage. These tests pin down
parser behaviour at the function level (no DB, no client) so future
refactors can't silently break filter preservation.
"""

from types import SimpleNamespace

from src.api.admin.people_merge import _parse_list_filters_from_hx_current_url


def _req(hx_current_url: str | None = None):
    """Build the minimal Request shape the parser needs: just `.headers.get`."""
    headers = {} if hx_current_url is None else {"HX-Current-URL": hx_current_url}
    return SimpleNamespace(headers=headers)


def test_missing_header_returns_defaults():
    assert _parse_list_filters_from_hx_current_url(_req()) == {
        "q": "",
        "status": "active",
        "page": 1,
        "page_size": 50,
    }


def test_empty_header_returns_defaults():
    assert _parse_list_filters_from_hx_current_url(_req("")) == {
        "q": "",
        "status": "active",
        "page": 1,
        "page_size": 50,
    }


def test_parses_q():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?q=Smith"))
    assert out["q"] == "Smith"


def test_strips_whitespace_from_q():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?q=%20%20Smith%20%20"))
    assert out["q"] == "Smith"


def test_parses_status_active():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?status=active"))
    assert out["status"] == "active"


def test_parses_status_archived():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?status=archived"))
    assert out["status"] == "archived"


def test_unknown_status_falls_back_to_active():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?status=banana"))
    assert out["status"] == "active"


def test_empty_status_falls_back_to_active():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?status="))
    assert out["status"] == "active"


def test_status_is_case_insensitive():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?status=ARCHIVED"))
    assert out["status"] == "archived"


def test_parses_page():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page=3"))
    assert out["page"] == 3


def test_page_clamped_to_at_least_one():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page=0"))
    assert out["page"] == 1


def test_negative_page_clamped_to_one():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page=-5"))
    assert out["page"] == 1


def test_garbage_page_falls_back_to_one():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page=abc"))
    assert out["page"] == 1


def test_parses_page_size():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page_size=25"))
    assert out["page_size"] == 25


def test_page_size_below_minimum_falls_back_to_default():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page_size=5"))
    assert out["page_size"] == 50


def test_page_size_above_maximum_falls_back_to_default():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page_size=9999"))
    assert out["page_size"] == 50


def test_garbage_page_size_falls_back_to_default():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/?page_size=xyz"))
    assert out["page_size"] == 50


def test_parses_all_four_at_once():
    out = _parse_list_filters_from_hx_current_url(
        _req("/admin/people/?q=Foo&status=archived&page=2&page_size=100"),
    )
    assert out == {"q": "Foo", "status": "archived", "page": 2, "page_size": 100}


def test_extra_unknown_params_ignored():
    out = _parse_list_filters_from_hx_current_url(
        _req("/admin/people/?q=Foo&flash=archived&utm_source=test"),
    )
    assert out["q"] == "Foo"
    assert out["status"] == "active"


def test_no_query_string():
    out = _parse_list_filters_from_hx_current_url(_req("/admin/people/"))
    assert out == {"q": "", "status": "active", "page": 1, "page_size": 50}
