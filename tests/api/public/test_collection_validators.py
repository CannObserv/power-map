"""Shared collection/catalog validator shapes (#392 PR-B).

Two shapes, no third (`docs/CONVENTIONS.md` § Public API):

- **watermark** — ``collection_etag``: ``count(*)`` + ``max(updated_at)`` over the
  *visible* set, every filter param baked in. Count catches a row entering or
  leaving the filtered set (an archived row's own ``updated_at`` bump is
  invisible when the filter excludes it); max catches an in-place edit.
- **content hash** — ``catalog_validator``: for a small, fully-materialized
  resource whose table carries no ``updated_at`` watermark at all. Exact by
  construction; saves serialization and transfer, not the query.
"""

from datetime import UTC, datetime

import pytest

from src.api.public.etag import catalog_validator, collection_etag

_LAST = datetime(2026, 8, 5, 12, 30, 45, tzinfo=UTC)


# ---------------------------------------------------------------------------
# collection_etag — watermark
# ---------------------------------------------------------------------------


def test_collection_etag_is_quoted_and_embeds_count_and_watermark():
    tag = collection_etag("01ABC-citations", 3, _LAST, 20, 0)
    assert tag.startswith('"') and tag.endswith('"')
    assert "-3-" in tag
    assert str(int(_LAST.timestamp() * 1000)) in tag


def test_collection_etag_empty_collection_uses_zero_watermark():
    """No rows means no max(updated_at) — still a stable, revalidatable tag."""
    assert collection_etag("01ABC-citations", 0, None, 20, 0) == '"01ABC-citations-0-0-20-0"'


@pytest.mark.parametrize(
    "left,right",
    [
        # a row added/removed from the filtered set
        ((3, _LAST, 20, 0), (4, _LAST, 20, 0)),
        # an in-place edit — count identical, watermark moved
        ((3, _LAST, 20, 0), (3, datetime(2026, 8, 5, 12, 30, 46, tzinfo=UTC), 20, 0)),
        # pagination window
        ((3, _LAST, 20, 0), (3, _LAST, 20, 20)),
        ((3, _LAST, 20, 0), (3, _LAST, 10, 0)),
    ],
)
def test_collection_etag_distinguishes(left, right):
    assert collection_etag("p", *left) != collection_etag("p", *right)


def test_collection_etag_separates_filter_params():
    """Adjacent params must not be able to fuse into the same tag (CR-proofing).

    ``(field="a", include=1)`` and ``(field="a1", include=None)`` are different
    result sets; a naive concatenation would collapse them.
    """
    assert collection_etag("p", 1, _LAST, "a", 1) != collection_etag("p", 1, _LAST, "a1", None)


def test_collection_etag_none_param_is_stable():
    """An omitted filter renders as a fixed token, not the string 'None' by accident."""
    tag = collection_etag("p", 1, _LAST, None, False)
    assert tag == collection_etag("p", 1, _LAST, None, False)
    assert tag != collection_etag("p", 1, _LAST, "", False)


def test_collection_etag_prefix_scopes_the_tag():
    """Two resources with equal shape must not collide."""
    assert collection_etag("a-citations", 1, _LAST, 20, 0) != collection_etag(
        "b-citations", 1, _LAST, 20, 0
    )


# ---------------------------------------------------------------------------
# catalog_validator — content hash
# ---------------------------------------------------------------------------


def test_catalog_validator_is_quoted_and_stable():
    rows = [{"id": "1", "slug": "a"}, {"id": "2", "slug": "b"}]
    tag = catalog_validator(rows)
    assert tag.startswith('"') and tag.endswith('"')
    assert tag == catalog_validator([dict(r) for r in rows])


def test_catalog_validator_detects_in_place_rename():
    """The whole point: `link_types` is admin-editable and carries no updated_at.

    A `count(*)` + `max(created_at)` tag is *stable* across a rename, so a 304ing
    consumer would hold the stale display_name forever.
    """
    before = [{"id": "1", "slug": "a", "display_name": "Alpha"}]
    after = [{"id": "1", "slug": "a", "display_name": "Alpha (renamed)"}]
    assert catalog_validator(before) != catalog_validator(after)


def test_catalog_validator_detects_add_remove_and_reorder():
    one = [{"id": "1"}]
    two = [{"id": "1"}, {"id": "2"}]
    assert catalog_validator(one) != catalog_validator(two)
    # Order is part of the representation — the routes all ORDER BY slug.
    assert catalog_validator(two) != catalog_validator(list(reversed(two)))


def test_catalog_validator_empty_is_stable_and_distinct():
    assert catalog_validator([]) == catalog_validator([])
    assert catalog_validator([]) != catalog_validator([{"id": "1"}])


def test_catalog_validator_distinguishes_field_boundaries():
    """Values must not fuse across columns — ``("ab","c")`` != ``("a","bc")``."""
    assert catalog_validator([{"x": "ab", "y": "c"}]) != catalog_validator([{"x": "a", "y": "bc"}])


def test_catalog_validator_distinguishes_none_from_empty_string():
    assert catalog_validator([{"x": None}]) != catalog_validator([{"x": ""}])


def test_catalog_validator_distinguishes_bool_from_int():
    """`expects_jurisdiction` is a bool column; a repr collision with 1/0 would blind it."""
    assert catalog_validator([{"x": True}]) != catalog_validator([{"x": 1}])
