"""Seed bcp47_locales + iso15924_scripts from langcodes + pycountry.

Idempotent — re-runs upsert into existing rows. Run once per environment
after schema apply:

    uv run --group seed scripts/seed_locales_scripts.py             # dry run
    uv run --group seed scripts/seed_locales_scripts.py --execute   # commit

Dry run by default (#402): the default `DATABASE_URL` is **production**, from
any directory, and this reads like a local dev command. The target is echoed
before the connection either way.

Library deps live in the `seed` dep group; this script is the only place
they are imported. Request-path code never validates these strings via
the libraries — the DB FK is the authoritative check.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Iterator

import asyncpg
import langcodes
import pycountry
from langcodes.tag_parser import LanguageTagError

from scripts._dsn import echo_target


def enumerate_bcp47_locales() -> Iterator[dict]:
    """Yield one record per CLDR locale.

    Each record: {code, language, script, region, display_name}.
    Codes are normalised by langcodes (e.g. 'en-us' → 'en-US').

    Source: langcodes.LIKELY_SUBTAGS — keys (partial tags like 'en') plus
    values (maximised tags like 'en-Latn-US'). After simplify_script(),
    the result is a deduplicated set of ~3500 locale codes covering CLDR.
    """
    seen: set[str] = set()
    candidates = list(langcodes.LIKELY_SUBTAGS.keys()) + list(langcodes.LIKELY_SUBTAGS.values())
    for code in candidates:
        try:
            tag = langcodes.Language.get(code).simplify_script()
        except (LanguageTagError, ValueError):
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


_UPSERT_LOCALES_SQL = """
    INSERT INTO bcp47_locales (code, language, script, region, display_name)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (code) DO UPDATE SET
        language     = EXCLUDED.language,
        script       = EXCLUDED.script,
        region       = EXCLUDED.region,
        display_name = EXCLUDED.display_name
"""

_UPSERT_SCRIPTS_SQL = """
    INSERT INTO iso15924_scripts (code, numeric_code, name)
    VALUES ($1, $2, $3)
    ON CONFLICT (code) DO UPDATE SET
        numeric_code = EXCLUDED.numeric_code,
        name         = EXCLUDED.name
"""


async def upsert_locales(conn: asyncpg.Connection, rows: Iterator[dict]) -> int:
    """Idempotent batch upsert into bcp47_locales. Returns row count processed."""
    payload = [
        (r["code"], r["language"], r["script"], r["region"], r["display_name"]) for r in rows
    ]
    if not payload:
        return 0
    await conn.executemany(_UPSERT_LOCALES_SQL, payload)
    return len(payload)


async def upsert_scripts(conn: asyncpg.Connection, rows: Iterator[dict]) -> int:
    """Idempotent batch upsert into iso15924_scripts. Returns row count processed."""
    payload = [(r["code"], r["numeric_code"], r["name"]) for r in rows]
    if not payload:
        return 0
    await conn.executemany(_UPSERT_SCRIPTS_SQL, payload)
    return len(payload)


_EXISTING_LOCALE_CODES_SQL = "SELECT code FROM bcp47_locales"
_EXISTING_SCRIPT_CODES_SQL = "SELECT code FROM iso15924_scripts"


async def preview(conn: asyncpg.Connection, sql: str, rows: list[dict]) -> tuple[int, int]:
    """Read-only classification for dry runs. Returns (would_insert, would_update).

    An upsert never deletes, so the split against the codes already present is
    the whole of what --execute would change.
    """
    existing = {r["code"] for r in await conn.fetch(sql)}
    codes = {r["code"] for r in rows}
    return len(codes - existing), len(codes & existing)


async def run(dsn: str, *, execute: bool) -> None:
    """Seed both lookup tables. Dry run (read-only preview) unless ``execute``."""
    echo_target(dsn)
    conn = await asyncpg.connect(dsn)
    try:
        # Materialized: the enumerations are generators, and a dry run reads
        # them for the preview while --execute reads them for the payload.
        scripts_ = list(enumerate_iso15924_scripts())
        locales = list(enumerate_bcp47_locales())

        if not execute:
            scr_new, scr_same = await preview(conn, _EXISTING_SCRIPT_CODES_SQL, scripts_)
            loc_new, loc_same = await preview(conn, _EXISTING_LOCALE_CODES_SQL, locales)
            # Diagnostics go to stderr alongside the target echo, so redirecting
            # one stream never leaves half the story.
            print(
                f"dry run: {loc_new} locales to insert, {loc_same} to update; "
                f"{scr_new} scripts to insert, {scr_same} to update. "
                "Pass --execute to commit.",
                file=sys.stderr,
            )
            return

        async with conn.transaction():
            # Scripts first: bcp47_locales.script FK references iso15924_scripts.
            n_scr = await upsert_scripts(conn, scripts_)
            n_loc = await upsert_locales(conn, locales)
        print(f"seeded: {n_loc} locales, {n_scr} scripts")
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="DSN to seed (default: DATABASE_URL — production).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit the upserts (default is a read-only dry run).",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("no database URL: pass --database-url or set DATABASE_URL")
    asyncio.run(run(args.database_url, execute=args.execute))


if __name__ == "__main__":
    main()
