"""Tests for csv_person source module."""

import csv
from pathlib import Path

import pytest

from src.core.ingestion.sources.csv_person import validate_person, transform_person


FIXTURE = Path("tests/fixtures/ingestion/people_sample.csv")


def read_fixture(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: v.strip() for k, v in row.items() if k is not None and isinstance(v, str)}
                for row in csv.DictReader(f)]


def test_validate_person_valid():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[0], source_row=2)
    assert r.ok


def test_validate_person_missing_name():
    r = validate_person({"Name": ""}, source_row=99)
    assert not r.ok
    assert any(e.field == "name" for e in r.errors)


async def test_transform_person_generates_id():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[0], source_row=2)
    result = await transform_person(r, source_reliability=0.8)
    assert result.ok
    assert len(result.transformed["person_id"]) == 26


async def test_transform_person_former_name():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[1], source_row=3)  # Bob Jones / Robert Jones
    result = await transform_person(r, source_reliability=0.8)
    names = result.transformed["names"]
    former = next((n for n in names if n["name_type"] == "former"), None)
    assert former is not None
    assert former["name"] == "Robert Jones"


async def test_transform_person_bad_email_warning():
    r = validate_person({"Name": "Test", "Personal Email": "bad@"}, source_row=99)
    result = await transform_person(r, source_reliability=0.8)
    assert result.ok
    assert not any(cm["contact_type"] == "email" for cm in result.transformed["contact_methods"])
    assert any("email" in w.lower() for w in result.warnings)


async def test_transform_person_pronouns():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[0], source_row=2)
    result = await transform_person(r, source_reliability=0.8)
    assert result.transformed["personal_pronouns"] == "she/her"


async def test_transform_person_confidence_records():
    rows = read_fixture(FIXTURE)
    r = validate_person(rows[0], source_row=2)
    result = await transform_person(r, source_reliability=0.8)
    records = result.transformed["confidence_records"]
    assert len(records) > 0
    for rec in records:
        assert rec.source_reliability == pytest.approx(0.8)
        assert rec.entity_id == result.transformed["person_id"]
