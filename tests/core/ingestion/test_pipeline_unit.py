"""Unit tests for pipeline module (no DB required)."""

from pathlib import Path

import pytest

from src.core.ingestion.pipeline import ImportConfig, _build_address_normalizer
from src.core.normalizers.address import AddressNormalizerConfig, FallbackAddressNormalizer


def test_import_config_invalid_reliability_raises():
    with pytest.raises(ValueError, match="source_reliability"):
        ImportConfig(
            orgs_csv=Path("a.csv"),
            people_csv=Path("b.csv"),
            roles_csv=Path("c.csv"),
            source_reliability=1.1,
        )


def test_import_config_negative_reliability_raises():
    with pytest.raises(ValueError, match="source_reliability"):
        ImportConfig(
            orgs_csv=Path("a.csv"),
            people_csv=Path("b.csv"),
            roles_csv=Path("c.csv"),
            source_reliability=-0.1,
        )


def test_import_config_boundary_reliability_ok():
    """0.0 and 1.0 are valid boundary values."""
    ImportConfig(
        orgs_csv=Path("a.csv"),
        people_csv=Path("b.csv"),
        roles_csv=Path("c.csv"),
        source_reliability=0.0,
    )
    ImportConfig(
        orgs_csv=Path("a.csv"),
        people_csv=Path("b.csv"),
        roles_csv=Path("c.csv"),
        source_reliability=1.0,
    )


# --------------------------------------------------------------------------- #
# local_addresses_only (#402)
# --------------------------------------------------------------------------- #


def test_local_addresses_only_defaults_off():
    config = ImportConfig(orgs_csv=Path("a.csv"), people_csv=Path("b.csv"), roles_csv=Path("c.csv"))
    assert config.local_addresses_only is False


@pytest.fixture
def keyed_normalizer(monkeypatch):
    """Pin the singleton to an API-key-configured normalizer.

    Addresses are standardized externally whenever ADDRESS_VALIDATOR_API_KEY is
    set — independent of validate_addresses — so this is the state in which
    local_only is the only lever that keeps a run off the wire.
    """
    monkeypatch.setattr(
        "src.core.ingestion.pipeline.get_address_normalizer",
        lambda: FallbackAddressNormalizer(config=AddressNormalizerConfig(api_key="k")),
    )


def test_build_address_normalizer_local_only_skips_the_external_service(keyed_normalizer):
    """A dry run must not spend the rate-limited validator quota (#402)."""
    normalizer = _build_address_normalizer(False, local_only=True)
    assert normalizer.config is None, "local_only must yield a config-less (local) normalizer"


def test_build_address_normalizer_local_only_overrides_validate_addresses(keyed_normalizer):
    """local_only wins: --validate-addresses on a dry run still stays local."""
    assert _build_address_normalizer(True, local_only=True).config is None


def test_build_address_normalizer_uses_the_service_when_not_local_only(keyed_normalizer):
    assert _build_address_normalizer(False, local_only=False).config is not None


# --------------------------------------------------------------------------- #
# 429 Retry-After cap (#597)
# --------------------------------------------------------------------------- #


@pytest.fixture
def shared_normalizer(monkeypatch):
    """Pin the singleton to one instance, as prod's admin form and public API see it."""
    shared = FallbackAddressNormalizer(
        config=AddressNormalizerConfig(
            api_key="k",
            base_url="https://validator.test",
            run_validation=True,
            transient_retries=1,
        )
    )
    monkeypatch.setattr("src.core.ingestion.pipeline.get_address_normalizer", lambda: shared)
    return shared


@pytest.mark.parametrize("validate_addresses", [False, True])
def test_build_address_normalizer_waits_out_long_retry_after(shared_normalizer, validate_addresses):
    """A bulk import honours Retry-After rather than fall back and leave rows for #595."""
    built = _build_address_normalizer(validate_addresses)
    assert built.config.retry_after_cap == 60.0


def test_build_address_normalizer_leaves_the_shared_cap_alone(shared_normalizer):
    """The admin form and public API keep the short cap; the import gets its own config."""
    built = _build_address_normalizer(False)
    assert built is not shared_normalizer
    assert shared_normalizer.config.retry_after_cap == 2.0


def test_build_address_normalizer_keeps_the_shared_settings(shared_normalizer):
    config = _build_address_normalizer(False).config
    assert config.api_key == "k"
    assert config.base_url == "https://validator.test"
    assert config.run_validation is True
    assert config.transient_retries == 1
