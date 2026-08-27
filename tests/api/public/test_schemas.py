"""Unit tests for public API response schemas and fmt_ts serializer."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.api.public.schemas import (
    OrgAcronym,
    OrgAffiliationType,
    OrgDetail,
    OrgIdentifier,
    OrgJurisdictionAffiliation,
    OrgLifespan,
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
    assert result == "2025-03-07T09:05:03.000000Z"


def testfmt_ts_fraction_is_fixed_width():
    """`YYYY-MM-DDTHH:MM:SS.ffffffZ` means six digits, always (CR #440/16).

    Bare `.isoformat()` omits the fraction entirely at whole seconds, so a
    client-supplied second-precision timestamp — `recorded_at` on an embedding,
    `accessed_at` on a citation — round-tripped as `…T12:00:00Z` and broke any
    consumer parsing with a fixed `%S.%fZ`. The width is the contract both
    `docs/PUBLIC_API.md` and AGENTS.md publish.
    """
    whole = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
    assert fmt_ts(whole) == "2026-08-14T12:00:00.000000Z"
    assert fmt_ts(whole.replace(microsecond=123000)) == "2026-08-14T12:00:00.123000Z"
    assert fmt_ts(whole.replace(microsecond=123456)) == "2026-08-14T12:00:00.123456Z"


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
    result = OrgSearchResult(id="abc", archived_at=dt, lifespan=OrgLifespan())
    dumped = result.model_dump(mode="json")
    assert dumped["archived_at"].endswith("Z")


def test_org_search_result_archived_at_none_stays_none():
    result = OrgSearchResult(id="abc", lifespan=OrgLifespan())
    dumped = result.model_dump(mode="json")
    assert dumped["archived_at"] is None


def test_org_search_result_optional_fields_default_none():
    r = OrgSearchResult(lifespan=OrgLifespan(), id="x")
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
        data=[OrgSearchResult(lifespan=OrgLifespan(), id="x", name="Foo")],
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
    detail = OrgDetail(
        lifespan=OrgLifespan(),
        id="abc",
        active=True,
        archived_at=_TS,
        created_at=_TS,
        updated_at=_TS,
    )
    dumped = detail.model_dump(mode="json")
    assert dumped["archived_at"].endswith("Z")


def test_org_detail_timestamps_serialized_to_z():
    detail = OrgDetail(
        lifespan=OrgLifespan(), id="abc", active=True, created_at=_TS, updated_at=_TS
    )
    dumped = detail.model_dump(mode="json")
    assert dumped["created_at"].endswith("Z")
    assert dumped["updated_at"].endswith("Z")


def test_org_detail_arrays_default_empty():
    detail = OrgDetail(
        lifespan=OrgLifespan(), id="abc", active=True, created_at=_TS, updated_at=_TS
    )
    assert detail.names == []
    assert detail.acronyms == []
    assert detail.identifiers == []


def test_org_detail_active_is_required():
    """active (#240) is required — no silent default masks a handler omission."""
    with pytest.raises(ValidationError):
        OrgDetail(lifespan=OrgLifespan(), id="abc", created_at=_TS, updated_at=_TS)


def test_org_detail_active_round_trips():
    true_dump = OrgDetail(
        lifespan=OrgLifespan(), id="abc", active=True, created_at=_TS, updated_at=_TS
    ).model_dump(mode="json")
    false_dump = OrgDetail(
        lifespan=OrgLifespan(), id="abc", active=False, created_at=_TS, updated_at=_TS
    ).model_dump(mode="json")
    assert true_dump["active"] is True
    assert false_dump["active"] is False


def test_org_detail_full_record_shape():
    detail = OrgDetail(
        lifespan=OrgLifespan(),
        id="abc",
        name="Foo Corp",
        acronym="FC",
        slug="fc",
        parent_id=None,
        archived_at=None,
        active=True,
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


# ---------------------------------------------------------------------------
# OrgJurisdictionAffiliation / OrgAffiliationType
# ---------------------------------------------------------------------------


def test_org_detail_jurisdiction_affiliations_default_empty():
    detail = OrgDetail(
        lifespan=OrgLifespan(), id="abc", active=True, created_at=_TS, updated_at=_TS
    )
    assert detail.jurisdiction_affiliations == []


def test_org_detail_jurisdiction_affiliations_shape():
    aff_type = OrgAffiliationType(id="t1", slug="governing", display_name="is governed by")
    aff = OrgJurisdictionAffiliation(jurisdiction_id="jid1", affiliation_type=aff_type)
    detail = OrgDetail(
        lifespan=OrgLifespan(),
        id="abc",
        active=True,
        created_at=_TS,
        updated_at=_TS,
        jurisdiction_affiliations=[aff],
    )
    dumped = detail.model_dump(mode="json")
    affs = dumped["jurisdiction_affiliations"]
    assert len(affs) == 1
    assert affs[0]["jurisdiction_id"] == "jid1"
    assert affs[0]["affiliation_type"]["slug"] == "governing"
    assert affs[0]["affiliation_type"]["display_name"] == "is governed by"


def test_org_affiliation_type_fields():
    t = OrgAffiliationType(id="x", slug="registered", display_name="is registered in")
    assert t.slug == "registered"
    assert t.display_name == "is registered in"
