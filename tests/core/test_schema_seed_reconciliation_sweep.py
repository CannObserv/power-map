"""Every seeded lookup with a UNIQUE natural key routes through the helper (#458).

The failure this guards is invisible in CI and fatal in production: a seed block
that conflicts on the PK alone aborts when an operator-created row already holds
the slug it is about to claim, and the abort takes ``ExecStartPre`` — and the
service — down with it. The exposure is created by *adding a row to an existing
seed*, which is exactly the edit that looks too small to think about.

So the rule is structural rather than per-table: a table pairing a ULID ``id``
PK with a UNIQUE natural key — however that key is spelled, and whatever it is
named — may not be seeded by a bare ``INSERT … VALUES``. Its rows go into a
``_seed_<table>`` staging table, ``reconcile_seeded_slugs()`` clears the way,
and the seed INSERT reads back from staging.
"""

import re
from pathlib import Path

SCHEMA = Path("src/core/schema.sql").read_text()

_CREATE_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL)


_INLINE_UNIQUE_RE = re.compile(
    r"^\s*(\w+)\s+(?:TEXT|VARCHAR\s*\(\d+\))\b[^,\n]*\bUNIQUE\b", re.MULTILINE
)
# Single-column and **total**: a partial unique (`… WHERE is_canonical`) is a
# per-parent slot, not a natural key — counting one would demand reconciliation
# of `organization_names` and `person_names`, which are not lookups at all.
_UNIQUE_INDEX_RE = re.compile(
    r"CREATE UNIQUE INDEX (?:CONCURRENTLY )?(?:IF NOT EXISTS )?\w+\s*\n?\s*"
    r"ON (\w+)\s*\(\s*(\w+)\s*\)([^;]*);",
    re.IGNORECASE,
)


def _tables_keyed_by_natural_key(sql: str = SCHEMA) -> dict[str, set[str]]:
    """Tables with a surrogate ``id`` PK, mapped to their UNIQUE non-PK columns.

    Both halves matter. A natural-key PK (``api_key_scope_types.id`` *is* the
    scope string; ``embedding_model_registry.model_id`` is the model name)
    cannot collide the way a minted ULID does, and a table with no second UNIQUE
    column has nothing to collide *on*.

    Both spellings of the second key count. Reading only the inline
    ``slug TEXT NOT NULL UNIQUE`` would miss a lookup whose key is declared out
    of line as ``CREATE UNIQUE INDEX`` — the spelling most of this schema's
    other unique keys use — and would miss one that calls the column anything
    but ``slug``. The guard is here because the exposure arrives in an
    unremarkable edit; it may not be blind to the ordinary way of writing one.
    """
    surrogate = {
        name: body
        for name, body in _CREATE_TABLE_RE.findall(sql)
        if re.search(r"^\s*id\s+TEXT\s+PRIMARY KEY", body, re.MULTILINE)
    }
    keyed = {
        name: {col for col in _INLINE_UNIQUE_RE.findall(body) if col != "id"}
        for name, body in surrogate.items()
    }
    for table, column, tail in _UNIQUE_INDEX_RE.findall(sql):
        if table in keyed and column != "id" and "WHERE" not in tail.upper():
            keyed[table].add(column)
    return {name: cols for name, cols in keyed.items() if cols}


def test_natural_key_lookups_are_recognised():
    """The extractor still sees the tables the rule is about."""
    keyed = _tables_keyed_by_natural_key()
    assert {
        "entity_identifier_types",
        "link_types",
        "entity_event_types",
        "jurisdiction_types",
        "role_types",
    } <= keyed.keys()
    assert keyed["entity_identifier_types"] == {"slug"}
    # A natural-key PK is out of scope — re-iding it would rename the key.
    assert "api_key_scope_types" not in keyed
    assert "embedding_model_registry" not in keyed


def test_no_natural_key_lookup_is_seeded_by_a_bare_values_insert():
    offenders = sorted(
        table
        for table in _tables_keyed_by_natural_key()
        if re.search(rf"INSERT INTO {table}\s*\([^;]*?\)\s*VALUES", SCHEMA, re.DOTALL)
    )
    assert offenders == [], (
        f"seed blocks conflict on the PK only, so these abort on an "
        f"operator-created duplicate slug: {offenders}. Stage the rows in "
        f"_seed_<table> and call reconcile_seeded_slugs() first (#458)."
    )


def test_every_staged_seed_is_reconciled_before_it_lands():
    staged = set(re.findall(r"CREATE TEMP TABLE (_seed_\w+)", SCHEMA))
    assert staged, "no staged seeds found — the seed section changed shape"

    for staging in staged:
        table = staging.removeprefix("_seed_")
        reconcile = SCHEMA.find(f"reconcile_seeded_slugs('{table}', '{staging}')")
        seed_insert = SCHEMA.find(f"FROM {staging}\n")
        assert reconcile != -1, f"{table} stages its seed but never reconciles it"
        assert seed_insert != -1, f"{staging} is staged but never seeds {table}"
        assert reconcile < seed_insert, (
            f"{table} reconciles after its seed INSERT — too late to matter"
        )
        assert f"DROP TABLE {staging}" in SCHEMA, f"{staging} outlives the seed block"


# The two spellings the extractor must not be blind to, as synthetic schemas —
# neither shape exists in schema.sql today, which is exactly why they need a
# test of their own rather than a live-tree assertion.
_OUT_OF_LINE_KEY = """
CREATE TABLE IF NOT EXISTS widget_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL,
    display_name TEXT        NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_types_slug
    ON widget_types (slug);
"""

_PARTIAL_UNIQUE = """
CREATE TABLE IF NOT EXISTS widget_names (
    id        TEXT    PRIMARY KEY,
    widget_id TEXT    NOT NULL,
    is_canonical BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_canonical
    ON widget_names (widget_id) WHERE is_canonical;
"""

_NON_SLUG_KEY = """
CREATE TABLE IF NOT EXISTS widget_types (
    id           TEXT        PRIMARY KEY,
    code         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL
);
"""

_NATURAL_KEY_PK = """
CREATE TABLE IF NOT EXISTS widget_types (
    model_id   TEXT        PRIMARY KEY,
    table_name TEXT        NOT NULL UNIQUE
);
"""


def test_extractor_sees_an_out_of_line_unique_index():
    """`CREATE UNIQUE INDEX` is how most of this schema spells a unique key."""
    assert _tables_keyed_by_natural_key(_OUT_OF_LINE_KEY) == {"widget_types": {"slug"}}


def test_extractor_sees_a_natural_key_not_called_slug():
    """The collision is with the UNIQUE column, whatever it is named."""
    assert _tables_keyed_by_natural_key(_NON_SLUG_KEY) == {"widget_types": {"code"}}


def test_extractor_ignores_a_partial_unique():
    """A per-parent slot is not a natural key — `organization_names` is not a lookup."""
    assert _tables_keyed_by_natural_key(_PARTIAL_UNIQUE) == {}


def test_extractor_ignores_a_natural_key_pk():
    """A PK that *is* the natural key cannot be re-idd onto anything."""
    assert _tables_keyed_by_natural_key(_NATURAL_KEY_PK) == {}
