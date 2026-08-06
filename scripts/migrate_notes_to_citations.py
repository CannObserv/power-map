"""Supervised migration: extract URL provenance from role_assignment notes into
structured citations (#319).

Before #319, citations were captured ad hoc in ``role_assignments.notes`` free
text (e.g. #314 folded housedemocrats.wa.gov links into Jinkins's Designate +
Speaker tenures). This script scans assignment notes for ``http(s)`` URLs and
creates a **whole-assignment** citation (``field_name`` NULL, ``url`` = the
extracted link, ``title`` = "migrated from assignment notes") for each — via the
idempotent natural-key observe path, so re-running never duplicates. The original
note text is **kept** (this adds structure, it does not rewrite notes).

Deliberately narrow and supervised (dry-run → ``--execute``), the #307/#311 audit
posture: it migrates only what it can extract unambiguously (bare URLs). Prose
provenance with no URL is left for human curation via the admin editor.

Usage:
    uv run python -m scripts.migrate_notes_to_citations            # dry-run report
    uv run python -m scripts.migrate_notes_to_citations --execute  # write citations
    uv run python -m scripts.migrate_notes_to_citations --assignment-id <id>
"""

import argparse
import asyncio
import re
import sys

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.citations import CitationClaim, apply_citation_observations
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Bare http(s) URL, trimmed of common trailing punctuation from surrounding prose.
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_TRAILING = ".,;:)]}"


def extract_urls(notes: str) -> list[str]:
    """Return de-duplicated URLs found in a note, order-preserving."""
    seen: list[str] = []
    for m in _URL_RE.finditer(notes):
        url = m.group(0).rstrip(_TRAILING)
        if url and url not in seen:
            seen.append(url)
    return seen


async def _run(database_url: str, execute: bool, assignment_id: str | None) -> int:
    conn = await asyncpg.connect(database_url)
    try:
        if assignment_id:
            rows = await conn.fetch(
                "SELECT id, notes FROM role_assignments WHERE id=$1 AND notes IS NOT NULL",
                assignment_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, notes FROM role_assignments"
                " WHERE notes IS NOT NULL AND notes ILIKE '%http%'"
            )

        planned = 0
        created = 0
        for row in rows:
            urls = extract_urls(row["notes"])
            if not urls:
                continue
            planned += len(urls)
            logger.info("assignment %s → %d URL(s): %s", row["id"], len(urls), ", ".join(urls))
            if not execute:
                continue
            claims = [CitationClaim(url=u, title="migrated from assignment notes") for u in urls]
            async with conn.transaction():
                results = await apply_citation_observations(
                    conn, "role_assignment", row["id"], None, claims
                )
            created += sum(1 for r in results if r.disposition.value in ("new", "updated"))

        if not execute:
            logger.info(
                "DRY-RUN: %d citation(s) would be created across %d note(s). "
                "Re-run with --execute.",
                planned,
                len(rows),
            )
        else:
            logger.info("Created/updated %d citation(s) from %d note(s).", created, len(rows))
        return 0
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute", action="store_true", help="Write citations (default: dry-run)."
    )
    parser.add_argument("--assignment-id", help="Limit to a single assignment id.")
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)

    configure_logging()
    sys.exit(asyncio.run(_run(dsn, args.execute, args.assignment_id)))


if __name__ == "__main__":
    main()
