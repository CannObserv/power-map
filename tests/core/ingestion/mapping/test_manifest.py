"""The ownership manifest — the contract #499 reads (#497 step 6, #499 step 1).

Every desired-state table declares its key, its retraction policy, the columns
the producer owns, and — since v2 — how a row binds to a PM table (`target`):
one of four shapes the applier interprets and nothing else. The loader is
typed so a binding missing what its shape needs fails at load, not at 09:30.
"""

import pytest
import yaml

pytest.importorskip("dbt.adapters.duckdb")

from src.core.ingestion.mapping import (  # noqa: E402
    PROJECT_DIR,
    load_manifest,
    write_desired_state,
)
from src.core.ingestion.mapping.manifest import (  # noqa: E402
    SHAPES,
    ManifestError,
    parse_manifest,
)
from src.core.ingestion.mapping.parquet import read_rows  # noqa: E402

MARTS = sorted(p.stem for p in (PROJECT_DIR / "models" / "marts").glob("*.sql"))


def _unique_columns(model: str) -> set[str]:
    """Columns of ``model`` carrying dbt's single-column `unique` test in schema.yml."""
    with (PROJECT_DIR / "models" / "schema.yml").open() as f:
        models = {m["name"]: m for m in yaml.safe_load(f)["models"]}
    unique = set()
    for col in models[model].get("columns", []):
        for test in col.get("tests", []):
            if test == "unique" or (isinstance(test, dict) and "unique" in test):
                unique.add(col["name"])
    return unique


def _raw() -> dict:
    with (PROJECT_DIR / "manifest.yml").open() as f:
        return yaml.safe_load(f)


def test_every_mart_is_declared_and_every_declaration_is_a_mart():
    manifest = load_manifest()

    assert sorted(manifest.tables) == MARTS


@pytest.mark.parametrize("table", MARTS)
def test_each_table_declares_key_retraction_and_owned_columns(table):
    spec = load_manifest().tables[table]

    assert spec.entity in {"person", "organization"}
    assert spec.key and all(isinstance(k, str) for k in spec.key)
    assert spec.retraction in {"none", "report", "archive"}
    assert isinstance(spec.owned_columns, list)


def test_events_own_exactly_dissolved():
    spec = load_manifest().tables["desired_entity_events"]

    assert spec.owned_event_types == ["dissolved"]
    assert spec.owned_columns == ["event_year"]  # month/day: PM holds finer precision on 5


def test_events_are_keyed_by_producer_id_like_every_other_table():
    """CR 19: an org create has no pm_id, so a key naming it could not identify its event."""
    spec = load_manifest().tables["desired_entity_events"]

    assert spec.key == ["producer_id", "event_type"]
    assert spec.pm_key == "pm_id"


@pytest.mark.parametrize("table", MARTS)
def test_no_key_names_pm_id(table):
    """`pm_id` is the row's *target*, resolved from the key — null for a create — never the key."""
    assert "pm_id" not in load_manifest().tables[table].key


def test_no_other_table_claims_event_types():
    tables = load_manifest().tables

    assert [t for t, s in tables.items() if s.owned_event_types] == ["desired_entity_events"]


# --- v2: the target binding (#499 step 1) -------------------------------------


@pytest.mark.parametrize("table", MARTS)
def test_every_table_binds_to_a_known_shape(table):
    target = load_manifest().tables[table].target

    assert target.shape in SHAPES
    if target.shape != "merge":
        assert target.table, f"{table}: shape {target.shape} names no PM table"


def test_the_four_shapes_cover_the_marts_as_designed():
    shapes = {t: s.target.shape for t, s in load_manifest().tables.items()}

    assert shapes == {
        "desired_people": "entity",
        "desired_organizations": "entity",
        "desired_organization_parents": "column",
        "desired_person_names": "child",
        "desired_organization_names": "child",
        "desired_organization_acronyms": "child",
        "desired_entity_events": "child",
        "desired_person_merges": "merge",
        "desired_organization_merges": "merge",
    }


@pytest.mark.parametrize("table", MARTS)
def test_owned_and_key_columns_are_bound_to_pm_columns(table):
    """A column the producer owns, or one that identifies the row, must map somewhere —
    an unbound owned column would be silently never written."""
    spec = load_manifest().tables[table]
    if spec.target.shape in ("entity", "merge"):
        return
    bound = set(spec.target.columns)

    assert set(spec.owned_columns) <= bound, f"{table}: owned {spec.owned_columns} vs {bound}"
    assert set(spec.key) - {"producer_id"} <= bound, f"{table}: key {spec.key} vs {bound}"


def test_child_bindings_declare_their_match_rule_and_its_needs():
    tables = load_manifest().tables
    names = tables["desired_person_names"].target
    events = tables["desired_entity_events"].target

    assert names.match == "any_then_canonical" and names.canonical == "is_canonical"
    assert names.type_column == "name_type"
    assert events.match == "key" and events.key_columns == ["event_type"]
    lookup = {"table": "entity_event_types", "from": "slug", "to": "id"}
    assert events.lookups["event_type"] == lookup
    assert events.constants == {"entity_type": "organization"}
    assert events.archived == "archived_at"


def test_thresholds_default_to_zero_creates_and_unlimited_updates():
    manifest = load_manifest()

    assert manifest.thresholds.creates == 0
    assert manifest.thresholds.merges == 0
    assert manifest.thresholds.conflicts == 0
    assert manifest.thresholds.stale == 0
    assert manifest.thresholds.updates is None
    assert manifest.streak == 3


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.__setitem__("shape", "blob"), "shape"),
        (lambda t: t.pop("table"), "table"),
        (lambda t: t.pop("parent"), "parent"),
        (lambda t: t.__setitem__("match", "nearest"), "match"),
        (lambda t: t.pop("canonical"), "canonical"),
    ],
    ids=["unknown shape", "no table", "child without parent", "unknown match", "no canonical"],
)
def test_a_binding_missing_what_its_shape_needs_fails_at_load(edit, message):
    raw = _raw()
    edit(raw["tables"]["desired_person_names"]["target"])

    with pytest.raises(ManifestError, match=message):
        parse_manifest(raw)


def test_a_key_match_needs_key_columns():
    raw = _raw()
    raw["tables"]["desired_entity_events"]["target"].pop("key_columns")

    with pytest.raises(ManifestError, match="key_columns"):
        parse_manifest(raw)


def test_a_table_without_a_target_fails_at_load():
    raw = _raw()
    raw["tables"]["desired_people"].pop("target")

    with pytest.raises(ManifestError, match="target"):
        parse_manifest(raw)


# --- write_desired_state ------------------------------------------------------


def test_write_desired_state_emits_one_parquet_per_declared_table(build, tmp_path):
    b = build()
    out = tmp_path / "desired_state"

    counts = write_desired_state(b.duckdb_path, out)

    assert sorted(counts) == MARTS
    assert sorted(p.stem for p in out.glob("*.parquet")) == MARTS
    assert counts["desired_entity_events"] == len(read_rows(out / "desired_entity_events.parquet"))
    assert list(out.glob(".incoming*")) == []


def test_write_desired_state_removes_parquet_the_manifest_no_longer_names(build, tmp_path):
    """CR 3: data/desired_state is #499's input; a ghost table reads as a live claim."""
    b = build()
    out = tmp_path / "desired_state"
    out.mkdir()
    ghost = out / "desired_ghost.parquet"
    ghost.write_bytes(b"not a table")
    unrelated = out / "README.txt"
    unrelated.write_text("mine")

    write_desired_state(b.duckdb_path, out)

    assert not ghost.exists()
    assert unrelated.exists()


@pytest.mark.parametrize("table", MARTS)
def test_the_projects_uniqueness_tests_match_the_declared_key(table):
    """CR 28: the manifest key is #499's contract; the tests must assert *that* key.

    A one-column key carries dbt's `unique`. A composite key carries a singular
    test on the tuple (tests/<table>_key_unique.sql) and no single-column
    `unique` on any of its columns — otherwise the day a second owned event
    type or name type lands, a legitimate row halts the build.
    """
    key = load_manifest().tables[table].key
    unique = _unique_columns(table)

    if len(key) == 1:
        assert key[0] in unique, f"{table}: key {key} has no unique test"
    else:
        assert not (set(key) & unique), f"{table}: composite key {key} but unique on {unique}"
        assert (PROJECT_DIR / "tests" / f"{table}_key_unique.sql").exists(), table
