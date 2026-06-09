"""Integration tests for the audit_address_precision migration script.

Note on "unclassifiable": the addresses.country column is NOT NULL, so it
is impossible to insert a row that would fall into the ELSE branch of the
precision CASE expression (which requires country IS NULL).  The tests for
the unclassifiable bucket therefore verify the constraint boundary rather
than a real data path.

For the country tier the test inserts a row with only country='US' and all
other structured fields NULL (address_line_1, postal_code, city, region all
NULL) and no raw_input.
"""

import asyncpg
import pytest
import pytest_asyncio

from scripts.audit_address_precision import backfill, classify
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _address(
    conn,
    *,
    address_line_1=None,
    postal_code=None,
    city=None,
    region=None,
    country="US",
    raw_input=None,
):
    """Insert a minimal address row with precision=NULL and return its id."""
    aid = generate_id()
    await conn.execute(
        "INSERT INTO addresses"
        " (id, address_line_1, postal_code, city, region, country, raw_input)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        aid,
        address_line_1,
        postal_code,
        city,
        region,
        country,
        raw_input,
    )
    return aid


# ---------------------------------------------------------------------------
# 1. Each tier classifies correctly
# ---------------------------------------------------------------------------


async def test_classify_street_tier(db):
    """address_line_1 present → street tier."""
    aid = await _address(db, address_line_1="123 Main St")
    buckets = await classify(db)
    assert aid in buckets["street"]


async def test_classify_postal_tier(db):
    """postal_code present, address_line_1 absent → postal tier."""
    aid = await _address(db, postal_code="90210")
    buckets = await classify(db)
    assert aid in buckets["postal"]


async def test_classify_city_tier(db):
    """city present, postal_code and address_line_1 absent → city tier."""
    aid = await _address(db, city="Beverly Hills")
    buckets = await classify(db)
    assert aid in buckets["city"]


async def test_classify_region_tier(db):
    """region present, city/postal_code/address_line_1 all absent → region tier."""
    aid = await _address(db, region="CA")
    buckets = await classify(db)
    assert aid in buckets["region"]


async def test_classify_country_tier(db):
    """Only country set, all other structured fields NULL → country tier."""
    aid = await _address(db, country="US")
    buckets = await classify(db)
    assert aid in buckets["country"]


# ---------------------------------------------------------------------------
# 2. Unclassifiable is detected
#
# Because addresses.country is NOT NULL, a row that falls through to the
# ELSE branch (which requires country IS NULL) cannot be inserted via normal
# SQL.  We verify this constraint boundary and confirm the bucket exists.
# ---------------------------------------------------------------------------


async def test_classify_unclassifiable_bucket_exists(db):
    """classify() always returns an 'unclassifiable' key (may be empty)."""
    buckets = await classify(db)
    assert "unclassifiable" in buckets
    assert isinstance(buckets["unclassifiable"], list)


async def test_classify_unclassifiable_requires_null_country(db):
    """Inserting with country=NULL raises NOT NULL violation — constraint enforced."""
    with pytest.raises(asyncpg.NotNullViolationError):
        await db.execute(
            "INSERT INTO addresses (id, raw_input, country) VALUES ($1, $2, NULL)",
            generate_id(),
            "only raw input",
        )


# ---------------------------------------------------------------------------
# 3. Execute mode sets precision
# ---------------------------------------------------------------------------


async def test_backfill_sets_precision(db):
    """Mixed addresses with NULL precision get correct precision after backfill."""
    street_id = await _address(db, address_line_1="1 Elm St")
    postal_id = await _address(db, postal_code="12345")
    city_id = await _address(db, city="Springfield")
    region_id = await _address(db, region="IL")
    country_id = await _address(db, country="US")

    updated = await backfill(db)
    assert updated >= 5

    expected = {
        street_id: "street",
        postal_id: "postal",
        city_id: "city",
        region_id: "region",
        country_id: "country",
    }
    for aid, tier in expected.items():
        precision = await db.fetchval("SELECT precision FROM addresses WHERE id = $1", aid)
        assert precision == tier, f"id={aid}: expected {tier!r}, got {precision!r}"


# ---------------------------------------------------------------------------
# 4. Execute mode is idempotent
# ---------------------------------------------------------------------------


async def test_backfill_idempotent(db):
    """Second backfill() call reports 0 updated rows."""
    await _address(db, address_line_1="2 Oak Ave")
    await _address(db, postal_code="99999")

    first = await backfill(db)
    assert first >= 2

    second = await backfill(db)
    assert second == 0


# ---------------------------------------------------------------------------
# 5. Execute mode skips already-set rows
# ---------------------------------------------------------------------------


async def test_backfill_skips_already_set_rows(db):
    """Row with precision already populated is not touched by backfill."""
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, country, precision) VALUES ($1, $2, $3, $4)",
        aid,
        "3 Pine Rd",
        "US",
        "street",
    )

    # Insert one new unset row so backfill has something to do.
    new_id = await _address(db, postal_code="55555")

    updated = await backfill(db)
    # Only the new row should be updated; the pre-set row is skipped.
    assert updated >= 1

    # Pre-set row untouched.
    precision = await db.fetchval("SELECT precision FROM addresses WHERE id = $1", aid)
    assert precision == "street"

    # New row was set.
    new_precision = await db.fetchval("SELECT precision FROM addresses WHERE id = $1", new_id)
    assert new_precision == "postal"


# ---------------------------------------------------------------------------
# 6. Execute mode leaves unclassifiable rows NULL
#
# The backfill UPDATE uses `AND tier_data.tier IS NOT NULL` so rows whose
# CASE returns NULL (i.e. unclassifiable) are never written.  Since the
# schema prevents country=NULL we cannot insert a truly unclassifiable row;
# instead we verify the SQL contract by confirming that a row already at
# NULL precision is only updated when it has a classifiable tier.
# ---------------------------------------------------------------------------


async def test_backfill_only_updates_classifiable_rows(db):
    """Rows that would be unclassifiable keep precision=NULL; classifiable rows are set."""
    # country-only row is the minimum classifiable row (tier='country').
    classifiable_id = await _address(db, country="US")

    # Insert a row with precision already set — simulates a row we don't want changed.
    preset_id = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, country, precision) VALUES ($1, $2, $3)",
        preset_id,
        "US",
        "country",
    )

    updated = await backfill(db)
    # Only the classifiable_id row (precision IS NULL) should be updated.
    assert updated >= 1

    precision_classifiable = await db.fetchval(
        "SELECT precision FROM addresses WHERE id = $1", classifiable_id
    )
    assert precision_classifiable == "country"

    precision_preset = await db.fetchval("SELECT precision FROM addresses WHERE id = $1", preset_id)
    assert precision_preset == "country"  # unchanged, was already set
