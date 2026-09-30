"""The marts' column types are declared, never inferred (#569).

Two ways a mart column gets a type nobody chose. A bare `NULL` — a literal, a
`CASE` with no typed branch — is `INTEGER` at bind time, whatever the rows; the
applier reads it by name as `None`, so the wrong type hides until a value
arrives (usa-wa#423 shipped such a model for its whole life). And a source read
without `all_varchar` takes its types from the data: a header-only CSV reads
`VARCHAR`, one with values `BIGINT`. So an empty store and a populated one are
both built, and both held to the types `manifest.yml` declares.
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
