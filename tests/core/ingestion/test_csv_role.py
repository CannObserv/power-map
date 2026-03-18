"""Tests for csv_role source module."""

import csv
from pathlib import Path

from src.core.ingestion.sources.csv_role import transform_role, validate_role

FIXTURE = Path("tests/fixtures/ingestion/roles_sample.csv")

ORG_INDEX = {"acme cannabis llc": "01ORGID00000000000000000001"}
PERSON_INDEX = {
    "jane smith": "01PERSONID0000000000000001",
    "bob jones": "01PERSONID0000000000000002",
}


def read_fixture(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: v.strip() for k, v in row.items() if k is not None and isinstance(v, str)}
                for row in csv.DictReader(f)]


def test_validate_role_valid():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    assert r.ok


def test_validate_role_missing_title():
    r = validate_role(
        {"Name": "Jane", "Organization": "Acme Cannabis LLC", "Title": ""}, source_row=99
    )
    assert not r.ok
    assert any(e.field == "title" for e in r.errors)


def test_transform_role_creates_role_and_assignment():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert result.ok
    t = result.transformed
    assert "role_id" in t
    assert "assignment_id" in t
    assert t["person_id"] == PERSON_INDEX["jane smith"]
    assert t["org_id"] == ORG_INDEX["acme cannabis llc"]


def test_transform_role_reuses_existing_role():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    existing_role_id = "01EXISTINGROLE000000000001"
    role_index = {(ORG_INDEX["acme cannabis llc"], "ceo"): existing_role_id}
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index=role_index, source_reliability=0.8)
    assert result.transformed["role_id"] == existing_role_id
    assert result.transformed["role_action"] == "matched"


def test_transform_role_unresolved_org_error():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)
    result = transform_role(r, org_index={}, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert not result.ok
    assert any("org" in e.message.lower() for e in result.errors)


def test_transform_role_is_current():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[0], source_row=2)  # "Yes"
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert result.transformed["is_current"] is True

    r2 = validate_role(rows[1], source_row=3)  # "No"
    result2 = transform_role(r2, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                             role_index={}, source_reliability=0.8)
    assert result2.transformed["is_current"] is False


def test_transform_role_bad_phone_warning():
    rows = read_fixture(FIXTURE)
    r = validate_role(rows[2], source_row=4)  # Bob Jones, bad-phone
    result = transform_role(r, org_index=ORG_INDEX, person_index=PERSON_INDEX,
                            role_index={}, source_reliability=0.8)
    assert result.ok
    assert not any(cm["contact_type"] == "phone" for cm in result.transformed["contact_methods"])
    assert any("phone" in w.lower() for w in result.warnings)
