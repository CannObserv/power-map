"""Unit tests for pipeline module (no DB required)."""

from pathlib import Path

import pytest

from src.core.ingestion.pipeline import ImportConfig


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
        orgs_csv=Path("a.csv"), people_csv=Path("b.csv"),
        roles_csv=Path("c.csv"), source_reliability=0.0,
    )
    ImportConfig(
        orgs_csv=Path("a.csv"), people_csv=Path("b.csv"),
        roles_csv=Path("c.csv"), source_reliability=1.0,
    )
