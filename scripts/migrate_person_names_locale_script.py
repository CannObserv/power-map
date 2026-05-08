"""Phase-1 backfill: populate `person_names.locale` / `person_names.script`.

Issue #135 — every existing `person_names` row predates the i18n columns
(`locale` BCP 47, `script` ISO 15924) and ships them NULL. Defaulting policy
delegates to `src.core.normalizers.person_name.suggest_locale_script`:

- pure-ASCII names → ``("en-US", "Latn")`` set on both columns
- Latin-with-diacritics (`Pedro García`, `João Castel-Branco Goulão`) →
  ``script='Latn'`` set; ``locale`` left NULL (the locale is a judgment
  call between es / pt / de / fr — escalated)
- non-Latin scripts (CJK, Cyrillic, Arabic, …) → both fields skipped and
  reported in the human-review list

For specific rows where a human has already picked the locale, pass
``_LOCALE_OVERRIDES`` (or the ``--locale-override id=tag`` CLI flag).
Overrides are applied alongside the bulk defaults — script auto-default
still runs.

Idempotent. Dry-run by default; ``--execute`` commits.

Usage:
    uv run python -m scripts.migrate_person_names_locale_script           # dry run
    uv run python -m scripts.migrate_person_names_locale_script --execute

Pre-conditions:
    * `DATABASE_URL` set
    * `apply_schema` run on the target DB
    * `scripts/seed_locales_scripts.py` run (FK lookup tables populated)

The script aborts with a non-zero exit if 'en-US' is missing from
`bcp47_locales` or 'Latn' is missing from `iso15924_scripts` — without
them the UPDATE would fail with a FK violation mid-batch.
"""

import argparse
import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass, field

import asyncpg

from src.core.logging import configure_logging, get_logger
from src.core.normalizers.person_name import suggest_locale_script

logger = get_logger(__name__)


@dataclass
class BackfillStats:
    """Summary of what was (or would be) updated."""

    locale_updated: int = 0
    script_updated: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = True
    locale_breakdown: dict[str, int] = field(default_factory=dict)
    script_breakdown: dict[str, int] = field(default_factory=dict)


# Per-row locale overrides for the bulk backfill.
#
# Each entry is a person_names.id whose row was surfaced by
# ``suggest_locale_script`` for human review (Latin-with-diacritics ->
# script='Latn' set, locale escalated). The mapping records the locale
# tag the operator picked during triage. Apply alongside the bulk
# defaults at --execute time. Override entries for rows whose ``locale``
# column is already set are ignored (no overwrite).
_LOCALE_OVERRIDES: dict[str, str] = {
    # João Castel-Branco Goulão — Brazilian Portuguese (issue #135 triage).
    "01KM1CTK10EW4YN2AP74VYQFVN": "pt-BR",
}


def _classify_rows(
    rows: list[dict | asyncpg.Record],
    *,
    locale_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[tuple[str, str]]]:
    """Bucket rows by suggested (locale, script) and collect rows to skip.

    Returns:
        (by_locale, by_script, skipped) where:
            by_locale: ``{locale_code: [name_id, ...]}`` — only rows whose
                ``locale`` column is currently NULL and either
                (a) the suggester returned a locale, or
                (b) the per-row override map names a locale.
            by_script: ``{script_code: [name_id, ...]}`` — same shape, for
                ``script`` (overrides do not apply here).
            skipped:   ``[(name_id, name), ...]`` — rows whose name is
                non-Latin script (and not overridden); escalated to a
                human in full.
    """
    overrides = locale_overrides or {}
    by_locale: dict[str, list[str]] = defaultdict(list)
    by_script: dict[str, list[str]] = defaultdict(list)
    skipped: list[tuple[str, str]] = []

    for r in rows:
        suggested_locale, suggested_script = suggest_locale_script(r["name"])
        override_locale = overrides.get(r["id"])
        # Effective locale: override wins over suggestion.
        effective_locale = override_locale or suggested_locale

        # If we have neither a script suggestion nor any locale signal, the
        # row is a full escalation (CJK / Cyrillic / etc).
        if suggested_script is None and effective_locale is None:
            skipped.append((r["id"], r["name"]))
            continue
        if r["locale"] is None and effective_locale is not None:
            by_locale[effective_locale].append(r["id"])
        if r["script"] is None and suggested_script is not None:
            by_script[suggested_script].append(r["id"])

    return dict(by_locale), dict(by_script), skipped


async def _preflight(
    conn: asyncpg.Connection,
    *,
    locale_overrides: dict[str, str] | None = None,
) -> None:
    """Verify the FK lookup tables contain every locale/script we'll write."""
    overrides = locale_overrides or {}
    needed_locales = {"en-US", *overrides.values()}
    missing: list[str] = []
    for code in sorted(needed_locales):
        if not await conn.fetchval(
            "SELECT 1 FROM bcp47_locales WHERE code=$1", code,
        ):
            missing.append(f"bcp47_locales[{code!r}]")
    if not await conn.fetchval(
        "SELECT 1 FROM iso15924_scripts WHERE code='Latn'"
    ):
        missing.append("iso15924_scripts['Latn']")
    if missing:
        raise SystemExit(
            f"Pre-flight failed — missing: {', '.join(missing)}. "
            "Run: uv run --group seed scripts/seed_locales_scripts.py"
        )


async def run_backfill(
    conn: asyncpg.Connection,
    *,
    dry_run: bool = True,
    locale_overrides: dict[str, str] | None = None,
) -> BackfillStats:
    """Backfill `person_names.locale` / `.script` on rows where they're NULL.

    Wraps everything in a savepoint so dry-run mode rolls back cleanly. The
    caller is responsible for the outer connection lifecycle (and for any
    enclosing transaction in test fixtures — savepoints nest fine).

    `locale_overrides` defaults to the module-level `_LOCALE_OVERRIDES`;
    pass an empty dict to disable.
    """
    if locale_overrides is None:
        locale_overrides = _LOCALE_OVERRIDES

    await _preflight(conn, locale_overrides=locale_overrides)

    rows = await conn.fetch(
        "SELECT id, name, locale, script FROM person_names"
        " WHERE locale IS NULL OR script IS NULL"
    )
    by_locale, by_script, skipped = _classify_rows(
        rows, locale_overrides=locale_overrides,
    )

    stats = BackfillStats(skipped=skipped, dry_run=dry_run)

    sp = conn.transaction()
    await sp.start()
    try:
        for locale_code, ids in by_locale.items():
            await conn.execute(
                "UPDATE person_names SET locale=$1 WHERE id = ANY($2::text[])",
                locale_code, ids,
            )
            stats.locale_updated += len(ids)
            stats.locale_breakdown[locale_code] = len(ids)
            logger.info(
                "backfill: set locale=%s on %d rows", locale_code, len(ids),
            )
        for script_code, ids in by_script.items():
            await conn.execute(
                "UPDATE person_names SET script=$1 WHERE id = ANY($2::text[])",
                script_code, ids,
            )
            stats.script_updated += len(ids)
            stats.script_breakdown[script_code] = len(ids)
            logger.info(
                "backfill: set script=%s on %d rows", script_code, len(ids),
            )
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()

    return stats


async def _main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes. Default is dry run (no changes made).",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL environment variable is required")

    conn = await asyncpg.connect(dsn)
    try:
        result = await run_backfill(conn, dry_run=dry_run)
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] person_names locale/script backfill:")
    print(f"  locale set on:  {result.locale_updated} rows")
    for code, count in sorted(result.locale_breakdown.items()):
        print(f"      {code:<10} {count:>5}")
    print(f"  script set on:  {result.script_updated} rows")
    for code, count in sorted(result.script_breakdown.items()):
        print(f"      {code:<10} {count:>5}")
    print(f"  skipped:        {len(result.skipped)} rows  (non-Latin — review)")
    if result.skipped:
        print("\n  Skipped rows (id, name):")
        for sid, name in result.skipped[:20]:
            print(f"    {sid}  {name!r}")
        if len(result.skipped) > 20:
            print(f"    … and {len(result.skipped) - 20} more")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
