"""Seed bcp47_locales + iso15924_scripts from langcodes + pycountry.

Idempotent — re-runs upsert into existing rows. Run once per environment
after schema apply:

    uv run --group seed scripts/seed_locales_scripts.py

Library deps live in the `seed` dep group; this script is the only place
they are imported. Request-path code never validates these strings via
the libraries — the DB FK is the authoritative check.
"""

import asyncio
import os
from collections.abc import Iterator

import asyncpg
import langcodes
import pycountry


def enumerate_bcp47_locales() -> Iterator[dict]:
    """Yield one record per CLDR locale.

    Each record: {code, language, script, region, display_name}.
    Codes are normalised by langcodes (e.g. 'en-us' → 'en-US').

    Source: langcodes.LIKELY_SUBTAGS — keys (partial tags like 'en') plus
    values (maximised tags like 'en-Latn-US'). After simplify_script(),
    the result is a deduplicated set of ~3500 locale codes covering CLDR.
    """
    seen: set[str] = set()
    candidates = list(langcodes.LIKELY_SUBTAGS.keys()) + list(
        langcodes.LIKELY_SUBTAGS.values()
    )
    for code in candidates:
        try:
            tag = langcodes.Language.get(code).simplify_script()
        except Exception:
            continue
        normalised = str(tag)
        if not normalised or normalised in seen:
            continue
        seen.add(normalised)
        yield {
            "code": normalised,
            "language": tag.language or "",
            "script": tag.script,
            "region": tag.territory,
            "display_name": tag.display_name(),
        }


def enumerate_iso15924_scripts() -> Iterator[dict]:
    """Yield one record per ISO 15924 script via pycountry."""
    for s in pycountry.scripts:
        yield {
            "code": s.alpha_4,
            "numeric_code": int(s.numeric),
            "name": s.name,
        }


async def upsert_locales(
    conn: asyncpg.Connection, rows: Iterator[dict]
) -> int:
    """Idempotent upsert into bcp47_locales. Returns row count processed."""
    count = 0
    for r in rows:
        await conn.execute(
            """
            INSERT INTO bcp47_locales (code, language, script, region, display_name)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (code) DO UPDATE SET
                language     = EXCLUDED.language,
                script       = EXCLUDED.script,
                region       = EXCLUDED.region,
                display_name = EXCLUDED.display_name
            """,
            r["code"],
            r["language"],
            r["script"],
            r["region"],
            r["display_name"],
        )
        count += 1
    return count


async def upsert_scripts(
    conn: asyncpg.Connection, rows: Iterator[dict]
) -> int:
    """Idempotent upsert into iso15924_scripts. Returns row count processed."""
    count = 0
    for r in rows:
        await conn.execute(
            """
            INSERT INTO iso15924_scripts (code, numeric_code, name)
            VALUES ($1, $2, $3)
            ON CONFLICT (code) DO UPDATE SET
                numeric_code = EXCLUDED.numeric_code,
                name         = EXCLUDED.name
            """,
            r["code"],
            r["numeric_code"],
            r["name"],
        )
        count += 1
    return count


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        n_loc = await upsert_locales(conn, enumerate_bcp47_locales())
        n_scr = await upsert_scripts(conn, enumerate_iso15924_scripts())
        print(f"seeded: {n_loc} locales, {n_scr} scripts")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
