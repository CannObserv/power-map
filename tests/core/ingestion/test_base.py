"""Tests for src.core.ingestion.base."""

from src.core.ingestion.base import ConfidenceRecord, FieldError, RowResult, value_hash


def test_value_hash_deterministic():
    assert value_hash("foo") == value_hash("foo")


def test_value_hash_different_inputs():
    assert value_hash("foo") != value_hash("bar")


def test_value_hash_is_hex_string():
    h = value_hash("test")
    assert len(h) == 64  # SHA-256 hex
    int(h, 16)  # raises if not hex


def test_row_result_ok_no_errors():
    r = RowResult(source_row=1, raw={"Name": "Acme"})
    assert r.ok is True


def test_row_result_ok_false_with_errors():
    r = RowResult(source_row=1, raw={}, errors=[FieldError(field="name", message="required")])
    assert r.ok is False


def test_confidence_record_fields():
    cr = ConfidenceRecord(
        entity_type="organization",
        entity_id="01ABC",
        field_name="phone",
        normalized_value="+12065551234",
        source_reliability=0.8,
        validation_status="unconfirmed",
        assessed_by="import:batch01",
    )
    assert cr.source_reliability == 0.8
