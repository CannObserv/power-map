"""Shared jurisdiction domain helpers.

Home for jurisdiction queries used by more than one surface. ``fetch_lineage``
is shared by the public lineage endpoint (#168) and the admin detail view (#275)
so the recursive-CTE traversal has a single definition.
"""

from typing import Any

# Display labels for jurisdiction_relationship_types.category (#278). Single
# source of truth for every surface that renders a category (admin templates
# via the `rel_category_label` Jinja filter; future public/graph surfaces). Keys
# must exactly match the schema CHECK enum — tests/core/test_jurisdictions.py
# enforces the sync. i18n, if it comes, hooks in here.
RELATIONSHIP_CATEGORY_LABELS: dict[str, str] = {
    "spatial": "Spatial",
    "governance": "Governance",
    "functional": "Functional",
    "lineage": "Lineage",
}


def relationship_category_label(slug: str) -> str:
    """Return the display label for a relationship-category slug.

    Unknown slugs fall back to title-cased words so rendering degrades
    gracefully if the schema enum grows before the mapping does (the sync
    test turns that drift into a failure).
    """
    return RELATIONSHIP_CATEGORY_LABELS.get(slug, slug.replace("_", " ").title())


_LINEAGE_SQL = """
WITH RECURSIVE lineage AS (
    SELECT
        j.id, j.slug, j.name,
        jt.id   AS type_id,
        jt.slug AS type_slug,
        jt.display_name AS type_display_name,
        j.valid_from, j.valid_until,
        j.recorded_at, j.superseded_at,
        j.created_at, j.updated_at, j.archived_at,
        0 AS depth,
        ARRAY[j.id] AS visited
    FROM jurisdictions j
    JOIN jurisdiction_types jt ON jt.id = j.type_id
    WHERE j.id = $1

    UNION ALL

    SELECT
        j2.id, j2.slug, j2.name,
        jt2.id, jt2.slug, jt2.display_name,
        j2.valid_from, j2.valid_until,
        j2.recorded_at, j2.superseded_at,
        j2.created_at, j2.updated_at, j2.archived_at,
        l.depth + 1,
        l.visited || j2.id
    FROM lineage l
    JOIN jurisdiction_relationships jr
        ON jr.from_id = l.id OR jr.to_id = l.id
    JOIN jurisdiction_relationship_types jrt
        ON jrt.id = jr.rel_type_id AND jrt.category = 'lineage'
    JOIN jurisdictions j2
        ON j2.id = CASE WHEN jr.from_id = l.id THEN jr.to_id ELSE jr.from_id END
    JOIN jurisdiction_types jt2 ON jt2.id = j2.type_id
    WHERE l.depth < $2
      AND NOT (j2.id = ANY(l.visited))
)
SELECT id, slug, name,
       type_id, type_slug, type_display_name,
       valid_from, valid_until,
       recorded_at, superseded_at,
       created_at, updated_at, archived_at,
       depth
FROM lineage
ORDER BY depth, id
"""


async def fetch_lineage(conn: Any, jurisdiction_id: str, depth: int = 10) -> list:
    """Return the lineage chain for a jurisdiction, anchored on a resolved id.

    Traverses ``category='lineage'`` edges (supersedes, evolved_from,
    merged_into) in both directions up to ``depth`` hops. Cycle-safe via a
    visited array. ``jurisdiction_id`` must be a resolved ULID — callers that
    accept a slug resolve it to an id first. Row 0 (``depth=0``) is the anchor
    jurisdiction itself; deeper rows are its lineage neighbours.
    """
    return await conn.fetch(_LINEAGE_SQL, jurisdiction_id, depth)
