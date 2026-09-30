"""The marts' column types are declared, never inferred (#569).

duckdb types a column from the values a SELECT yields. An expression that is
NULL on every row — an empty store, an all-NULL `CASE` branch — falls back to
`INTEGER`, and the applier reads it by name as `None` either way, so the wrong
type hides until a value arrives. usa-wa shipped an always-empty model typed
`integer` for its whole life (usa-wa#423). The empty store is the build that
exposes inference; the populated one hides it, so both are held to the types
`manifest.yml` declares.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from src.core.ingestion.mapping import USA_WA_SOURCES, load_manifest  # noqa: E402
from tests.core.ingestion.mapping.conftest import FIXTURE_STORE, FIXTURE_VERSION  # noqa: E402


def _empty_store() -> dict:
    """``build`` arguments for a store holding every input with zero rows."""
    datasets = {
        name: (FIXTURE_STORE / name / FIXTURE_VERSION / "data.csv").read_text().splitlines()[0]
        + "\n"
        for name in USA_WA_SOURCES
    }
    return {"crosswalk": (), "overlay": (), "role_types": (), "datasets": datasets}


def _declared() -> dict[str, dict[str, str]]:
    return {name: spec.types for name, spec in load_manifest().tables.items()}


def _marts(types: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {name: cols for name, cols in types.items() if name.startswith("desired_")}


def test_an_empty_store_builds_every_mart_with_its_declared_types(build):
    assert _marts(build(**_empty_store()).types()) == _declared()


def test_a_populated_store_builds_every_mart_with_its_declared_types(build):
    assert _marts(build().types()) == _declared()


def test_no_model_changes_type_between_an_empty_and_a_populated_store(build):
    """Staging and intermediate models too: a type inferred upstream flows into a mart."""
    assert build(**_empty_store()).types() == build().types()
