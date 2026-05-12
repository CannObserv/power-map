"""Unit tests for pure helpers in orgs_addresses (no DB required)."""

import inspect

import pytest

import src.api.admin.orgs_addresses as orgs_addresses_module
from src.api.admin.orgs_addresses import _parse_normalizer_fields


def test_init_normalizer_deleted_from_module_namespace():
    assert not hasattr(orgs_addresses_module, "_init_normalizer")


def test_address_create_mode_form_default_is_confirm():
    sig = inspect.signature(orgs_addresses_module.address_create)
    assert sig.parameters["mode"].default.default == "confirm"


def test_address_edit_row_post_mode_form_default_is_confirm():
    sig = inspect.signature(orgs_addresses_module.address_edit_row_post)
    assert sig.parameters["mode"].default.default == "confirm"


def test_parse_normalizer_fields_valid():
    std, lat, lon, comp = _parse_normalizer_fields(
        " 123 MAIN ST ",
        "47.6062",
        "-122.3321",
        '{"spec":"usps-pub28","spec_version":"unknown","values":{}}',
    )
    assert std == "123 MAIN ST"
    assert lat == 47.6062
    assert lon == -122.3321
    assert comp == '{"spec":"usps-pub28","spec_version":"unknown","values":{}}'


def test_parse_normalizer_fields_all_empty():
    std, lat, lon, comp = _parse_normalizer_fields("", "", "", "")
    assert std is None
    assert lat is None
    assert lon is None
    assert comp is None


def test_parse_normalizer_fields_invalid_latitude():
    with pytest.raises(ValueError):
        _parse_normalizer_fields("123 MAIN ST", "not-a-float", "", "")


def test_parse_normalizer_fields_invalid_longitude():
    with pytest.raises(ValueError):
        _parse_normalizer_fields("123 MAIN ST", "", "nope", "")


def test_parse_normalizer_fields_invalid_components_json():
    with pytest.raises(ValueError):
        _parse_normalizer_fields("123 MAIN ST", "", "", "{bad json")
