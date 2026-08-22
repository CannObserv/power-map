"""Every seeded lookup with a UNIQUE natural key routes through the helper (#458).

The failure this guards is invisible in CI and fatal in production: a seed block
that conflicts on the PK alone aborts when an operator-created row already holds
the slug it is about to claim, and the abort takes ``ExecStartPre`` — and the
service — down with it. The exposure is created by *adding a row to an existing
seed*, which is exactly the edit that looks too small to think about.

So the rule is structural rather than per-table: a table pairing a ULID ``id``
PK with a UNIQUE ``slug`` may not be seeded by a bare ``INSERT … VALUES``. Its
rows go into a ``_seed_<table>`` staging table, ``reconcile_seeded_slugs()``
clears the way, and the seed INSERT reads back from staging.
"""

import re
from pathlib import Path

SCHEMA = Path("src/core/schema.sql").read_text()

_CREATE_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL)


def _tables_keyed_by_slug() -> set[str]:
    """Tables with a surrogate ``id`` PK *and* a UNIQUE ``slug``.

    Both halves matter. A natural-key PK (``api_key_scope_types.id`` is the
    scope string) cannot collide the way a minted ULID does, and a table with no
    second UNIQUE column has nothing to collide *on*.
    """
    keyed = set()
    for name, body in _CREATE_TABLE_RE.findall(SCHEMA):
        if not re.search(r"^\s*id\s+TEXT\s+PRIMARY KEY", body, re.MULTILINE):
            continue
        if re.search(r"^\s*slug\s+TEXT\s+.*UNIQUE", body, re.MULTILINE):
            keyed.add(name)
    return keyed


def test_slug_keyed_lookups_are_recognised():
    """The extractor still sees the tables the rule is about."""
    keyed = _tables_keyed_by_slug()
    assert {
        "entity_identifier_types",
        "link_types",
        "entity_event_types",
        "jurisdiction_types",
        "role_types",
    } <= keyed
    # A natural-key PK is out of scope — re-iding it would rename the key.
    assert "api_key_scope_types" not in keyed


def test_no_slug_keyed_lookup_is_seeded_by_a_bare_values_insert():
    offenders = sorted(
        table
        for table in _tables_keyed_by_slug()
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
