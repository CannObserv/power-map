"""Tests for csv_org source module."""

import csv
from pathlib import Path

import pytest

from src.core.ingestion.sources.csv_org import transform_org, validate_org

FIXTURE = Path("tests/fixtures/ingestion/orgs_sample.csv")


def read_fixture(path: Path) -> list[dict[str, str]]:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({
                k: v.strip() for k, v in row.items()
                if k is not None and isinstance(v, str)
            })
    return rows


def test_validate_org_valid_row():
    rows = read_fixture(FIXTURE)
    result = validate_org(rows[0], source_row=2)
    assert result.ok
    assert result.errors == []


def test_validate_org_missing_name():
    result = validate_org({"Name": "", "Active?": "Yes"}, source_row=99)
    assert not result.ok
    assert any(e.field == "name" for e in result.errors)


def test_validate_org_empty_row():
    result = validate_org({}, source_row=99)
    assert not result.ok


async def test_transform_org_generates_id():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    org_index: dict[str, str] = {}
    result = await transform_org(r, org_index=org_index, source_reliability=0.8)
    assert result.ok
    assert "org_id" in result.transformed
    assert len(result.transformed["org_id"]) == 26


async def test_transform_org_canonical_name():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    names = result.transformed["names"]
    legal = next(n for n in names if n["name_type"] == "legal")
    assert legal["name"] == "Acme Cannabis LLC"
    assert legal["is_canonical"] is True


async def test_transform_org_acronym():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    names = result.transformed["names"]
    acronym = next((n for n in names if n["name_type"] == "acronym"), None)
    assert acronym is not None
    assert acronym["name"] == "AC"


async def test_transform_org_parent_resolved():
    parent_id = "01TESTPARENTID00000000000A"
    rows = read_fixture(FIXTURE)
    child_row = rows[1]  # "Child Org" with Parent Organization = "Acme Cannabis LLC"
    r = validate_org(child_row, source_row=3)
    org_index = {"acme cannabis llc": parent_id}
    result = await transform_org(r, org_index=org_index, source_reliability=0.8)
    assert result.transformed["parent_id"] == parent_id


async def test_transform_org_unresolved_parent_warning():
    rows = read_fixture(FIXTURE)
    child_row = rows[1]
    r = validate_org(child_row, source_row=3)
    org_index: dict[str, str] = {}  # empty — parent not found
    result = await transform_org(r, org_index=org_index, source_reliability=0.8)
    assert result.ok  # entity still loads, warning logged
    assert any("parent" in w.lower() for w in result.warnings)
    assert result.transformed["parent_id"] is None


async def test_transform_org_bad_phone_warning():
    rows = read_fixture(FIXTURE)
    bad_row = rows[2]  # "Bad Phone Org"
    r = validate_org(bad_row, source_row=4)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    assert result.ok
    contact_methods = result.transformed["contact_methods"]
    assert not any(cm["contact_type"] == "phone" for cm in contact_methods)
    assert any("phone" in w.lower() for w in result.warnings)


async def test_transform_org_confidence_records():
    rows = read_fixture(FIXTURE)
    r = validate_org(rows[0], source_row=2)
    result = await transform_org(r, org_index={}, source_reliability=0.8)
    records = result.transformed["confidence_records"]
    assert len(records) > 0
    for rec in records:
        assert rec.source_reliability == pytest.approx(0.8)
        assert rec.entity_id == result.transformed["org_id"]
