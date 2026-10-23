"""Helpers shared by the settings lookup-table views (identifier types, link types)."""

import asyncpg


async def write_unique_slug(db, slug: str, sql: str, *args) -> str | None:
    """Run an INSERT/UPDATE under a savepoint; a slug clash becomes a form error (#687).

    Returns the curator-facing error message, or None on success. The savepoint
    aborts only this write, keeping the ambient transaction (and so the error
    response) usable (#288).
    """
    try:
        async with db.transaction():
            await db.execute(sql, *args)
    except asyncpg.UniqueViolationError:
        return f"Slug \u201c{slug}\u201d is already in use."
    return None
