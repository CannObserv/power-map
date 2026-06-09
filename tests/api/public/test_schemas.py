"""Unit tests for public API response schemas and fmt_ts serializer."""

from datetime import UTC, datetime

from src.api.public.schemas import (
    OrgAcronym,
    OrgDetail,
    OrgIdentifier,
    OrgName,
    OrgSearchResponse,
    OrgSearchResult,
    SearchMeta,
    fmt_ts,
)

# ---------------------------------------------------------------------------
# fmt_ts
# ---------------------------------------------------------------------------


def testfmt_ts_none_returns_none():
    assert fmt_ts(None) is None


def testfmt_ts_utc_aware_ends_with_z():
    dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = fmt_ts(dt)
    assert result is not None
    assert result.endswith("Z"), f"expected Z suffix, got {result!r}"


def testfmt_ts_utc_aware_no_plus_offset():
    dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    result = fmt_ts(dt)
    assert "+00:00" not in result


def testfmt_ts_preserves_microseconds():
    dt = datetime(2024, 6, 15, 12, 0, 0, 123456, tzinfo=UTC)
    result = fmt_ts(dt)
    assert "123456" in result


def testfmt_ts_formats_date_components_correctly():
    dt = datetime(2025, 3, 7, 9, 5, 3, tzinfo=UTC)
    result = fmt_ts(dt)
    assert result == "2025-03-07T09:05:03Z"


def testfmt_ts_naive_datetime_returns_no_z_suffix():
    # All DB timestamps are TIMESTAMPTZ (UTC-aware), so naive datetimes cannot
    # reach fmt_ts in practice. This test documents the edge-case behaviour:
    # no timezone info → no +00:00 replacement → no Z suffix in output.
    dt = datetime(2024, 1, 1)  # naive, no tzinfo
    result = fmt_ts(dt)
    assert result is not None
    assert not result.endswith("Z"), f"naive datetime should not produce Z suffix, got {result!r}"


# ---------------------------------------------------------------------------
# OrgSearchResult
# ---------------------------------------------------------------------------


def test_org_search_result_archived_at_serialized_to_z():
    dt = datetime(2024, 1, 1, tzinfo=UTC)
    result = OrgSearchResult(id="abc", archived_at=dt)
    dumped = result.model_dump(mode="json")
    assert dumped["archived_at"].endswith("Z")


def test_org_search_result_archived_at_none_stays_none():
    result = OrgSearchResult(id="abc")
    dumped = result.model_dump(mode="json")
    assert dumped["archived_at"] is None


def test_org_search_result_optional_fields_default_none():
    r = OrgSearchResult(id="x")
    assert r.name is None
    assert r.acronym is None
    assert r.slug is None
    assert r.parent_id is None
    assert r.archived_at is None


# ---------------------------------------------------------------------------
# SearchMeta
# ---------------------------------------------------------------------------


def test_org_search_meta_all_fields():
    meta = SearchMeta(limit=10, offset=0, count=3, has_more=False)
    assert meta.limit == 10
    assert meta.offset == 0
    assert meta.count == 3
    assert meta.has_more is False


# ---------------------------------------------------------------------------
# OrgSearchResponse envelope
# ---------------------------------------------------------------------------


def test_org_search_response_shape():
    response = OrgSearchResponse(
        data=[OrgSearchResult(id="x", name="Foo")],
        meta=SearchMeta(limit=10, offset=0, count=1, has_more=False),
    )
    dumped = response.model_dump(mode="json")
    assert "data" in dumped
    assert "meta" in dumped
    assert len(dumped["data"]) == 1
    assert dumped["data"][0]["name"] == "Foo"


def test_org_search_response_empty_data():
    response = OrgSearchResponse(
        data=[],
        meta=SearchMeta(limit=10, offset=0, count=0, has_more=False),
    )
    assert response.data == []
    assert response.meta.count == 0


# ---------------------------------------------------------------------------
# OrgDetail
# ---------------------------------------------------------------------------


_TS = datetime(2024, 1, 1, tzinfo=UTC)


def test_org_detail_inherits_archived_at_serializer():
    detail = OrgDetail(id="abc", archived_at=_TS, created_at=_TS, updated_at=_TS)
    dumped = detail.model_dump(mode="json")
    assert dumped["archived_at"].endswith("Z")


def test_org_detail_timestamps_serialized_to_z():
    detail = OrgDetail(id="abc", created_at=_TS, updated_at=_TS)
    dumped = detail.model_dump(mode="json")
    assert dumped["created_at"].endswith("Z")
    assert dumped["updated_at"].endswith("Z")


def test_org_detail_arrays_default_empty():
    detail = OrgDetail(id="abc", created_at=_TS, updated_at=_TS)
    assert detail.names == []
    assert detail.acronyms == []
    assert detail.identifiers == []


def test_org_detail_full_record_shape():
    detail = OrgDetail(
        id="abc",
        name="Foo Corp",
        acronym="FC",
        slug="fc",
        parent_id=None,
        archived_at=None,
        created_at=_TS,
        updated_at=_TS,
        names=[OrgName(id="n1", name="Foo Corp", name_type="legal", is_canonical=True)],
        acronyms=[OrgAcronym(id="a1", acronym="FC", is_canonical=True)],
        identifiers=[OrgIdentifier(id="i1", type_id="t1", type_slug="ein", value="12-3456789")],
    )
    dumped = detail.model_dump(mode="json")
    assert dumped["names"][0]["name_type"] == "legal"
    assert dumped["acronyms"][0]["is_canonical"] is True
    assert dumped["identifiers"][0]["type_slug"] == "ein"
