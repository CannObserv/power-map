"""Classify legacy WA legislative roles onto the #266 role-type vocabulary.

Types the free-text committee / chamber / legislative-staff roles that predate
#266, so "all committee chairs", "all legislative staff" etc. aggregate without
matching titles. Scope is **WA only** — federal legislative roles and
caucus/floor-leadership vocab are deliberately out (see #266).

Four phases, in order (later phases assume earlier ones ran):

1. **Curate collisions.** A few titles are two spellings of one office, and
   normalizing them in code would violate ``uq_role_org_title``. Fixed in the
   data instead: within one org, ``Ranking Minority Member`` merges into
   ``Ranking Member`` and the ``Reseach Analyst`` typo merges into
   ``Research Analyst`` — assignments are **re-pointed** (never deleted), then
   the drained role is archived. ``1st Vice Chair`` is a collision-free rename to
   ``First Vice Chair``. Merges only fire when both titles exist on the same org.
2. **Committee orgs** (those carrying ``org_wa_legislature_committee_id``):
   officeholder titles map to the ``committee_*`` vocab; everything else on a
   committee is staff → ``legislature_staff``, keeping its specific title.
3. **Legislative staff offices** (OPR, Senate Committee Services): untyped roles
   → ``legislature_staff``.
4. **Chamber backlog** — the enumerated House/Senate rows: Speaker →
   ``chamber_leader``, Secretary of the Senate → ``chamber_officer``, personal and
   office staff → ``legislature_staff``, with per-row retitling, principal→notes,
   and the three staff-office re-homes.

**Titles are preserved wherever normalizing would erase a real distinction**
(``Acting Chair`` keeps its title and takes ``committee_chair``): the coarse type
carries the aggregation, the title carries the history.

**Dates are never invented (#307).** The Speaker row's ``(2021-23)`` tenure is
moved out of the title into role notes and reported — a human sets the assignment
dates and currency, because "2021-23" doesn't determine exact bounds and the
assignment is currently ``is_current=TRUE``.

Idempotent: a classified role no longer matches an untyped query, a merged role
is archived, and renames are keyed on the pre-rename title.

Usage:
    uv run python -m scripts.classify_legislative_roles            # dry run
    uv run python -m scripts.classify_legislative_roles --execute  # commit
"""

import argparse
import asyncio
from collections import Counter
from typing import Literal, NamedTuple, TypedDict

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# --- Phase 1: collision curation -------------------------------------------

# (variant_title, canonical_title) — merged only when BOTH exist on one org.
_TITLE_MERGES: tuple[tuple[str, str], ...] = (
    ("Ranking Minority Member", "Ranking Member"),
    ("Reseach Analyst", "Research Analyst"),
)

# Collision-free retitles (no merge needed).
_TITLE_RENAMES: dict[str, str] = {"1st Vice Chair": "First Vice Chair"}

# --- Phase 2: committee vocabulary -----------------------------------------

# lower(title) -> role_type slug. Anything on a committee org that isn't here is
# committee staff. `Acting Chair` / `First Vice Chair` keep their titles: the type
# carries the aggregation, the title the distinction.
_COMMITTEE_OFFICEHOLDER_TYPES: dict[str, str] = {
    "chair": "committee_chair",
    "acting chair": "committee_chair",
    "vice chair": "committee_vice_chair",
    "first vice chair": "committee_vice_chair",
    "ranking member": "committee_ranking_member",
    # Variant spellings type correctly even when they survive Phase 1 (no
    # canonical row on their org to merge into) — without these a lone variant
    # would fall through to the staff default.
    "ranking minority member": "committee_ranking_member",
    "ranking democratic member": "committee_ranking_member",
    "assistant ranking member": "committee_assistant_ranking_member",
    "member": "committee_member",
}

# Not roles — observation artifacts. Left untouched for the #304 data sweep.
_NON_ROLE_TITLES = frozenset({"guest", "visitor or guest", "participant"})

# --- Phase 3/4: specific orgs ----------------------------------------------

_ORG_WA_HOUSE_OPR = "01KV6PQGAAR5SDJH6H6BXSYYQT"
_ORG_WA_SCS = "01KXRJQBBD2RCBJXZJ6P5DG726"
_ORG_WA_HOUSE_COG = "01KWJP0WVH7PR7E77TZN60TXCJ"  # canonical post-#305 merge

# Legislative staff offices: every untyped role on these is legislature_staff.
_STAFF_OFFICE_ORG_IDS: tuple[str, ...] = (_ORG_WA_HOUSE_OPR, _ORG_WA_SCS)


class BacklogRule(NamedTuple):
    """Per-row rule for the enumerated chamber backlog, keyed on exact title."""

    title: str
    role_type: str
    new_title: str | None = None
    rehome_org_id: str | None = None
    notes: str | None = None
    # Tenure embedded in the title that must move to notes, not to invented dates.
    flag_tenure: bool = False


_BACKLOG_RULES: tuple[BacklogRule, ...] = (
    BacklogRule(
        title="Speaker of the House (2021-23)",
        role_type="chamber_leader",
        new_title="Speaker of the House",
        notes="Tenure 2021-23 (from legacy title) — assignment dates need review",
        flag_tenure=True,
    ),
    BacklogRule(title="Secretary of the Senate", role_type="chamber_officer"),
    BacklogRule(
        title="Legislative Aide, Senator June Robinson",
        role_type="legislature_staff",
        new_title="Legislative Aide",
        notes="Aide to Senator June Robinson",
    ),
    BacklogRule(
        title="Legislative Assistant, Rep. Shelley Kloba",
        role_type="legislature_staff",
        new_title="Legislative Assistant",
        notes="Assistant to Representative Shelley Kloba",
    ),
    BacklogRule(
        title="Legislative Assistant to Senator Saldaña",
        role_type="legislature_staff",
        new_title="Legislative Assistant",
        notes="Assistant to Senator Saldaña",
    ),
    BacklogRule(
        title="Office of Program Research Director",
        role_type="legislature_staff",
        new_title="Director",
        rehome_org_id=_ORG_WA_HOUSE_OPR,
    ),
    BacklogRule(
        title="Senate Committee Services Director",
        role_type="legislature_staff",
        new_title="Director",
        rehome_org_id=_ORG_WA_SCS,
    ),
    BacklogRule(title="Senior Policy Analyst", role_type="legislature_staff"),
    BacklogRule(
        title="Senior Policy Analyst, WA House COG",
        role_type="legislature_staff",
        new_title="Senior Policy Analyst",
        rehome_org_id=_ORG_WA_HOUSE_COG,
    ),
)

ActionKind = Literal["merged", "renamed", "classified", "skipped", "conflict"]


class Action(TypedDict):
    """One planned or applied mutation."""

    kind: ActionKind
    role_id: str
    title: str
    detail: str


class Report(TypedDict):
    """Full run outcome."""

    actions: list[Action]


_COMMITTEE_ORG_PREDICATE = """
EXISTS (
    SELECT 1 FROM identifiers i
    JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
    WHERE i.entity_id = r.organization_id AND t.slug = 'org_wa_legislature_committee_id'
)
"""

_UNTYPED_ON_COMMITTEE_SQL = f"""
SELECT r.id, r.organization_id, r.title
FROM roles r
WHERE r.role_type_id IS NULL AND r.jurisdiction_id IS NULL AND r.archived_at IS NULL
  AND {_COMMITTEE_ORG_PREDICATE}
ORDER BY r.title, r.id
"""

_UNTYPED_ON_ORGS_SQL = """
SELECT r.id, r.organization_id, r.title
FROM roles r
WHERE r.role_type_id IS NULL AND r.jurisdiction_id IS NULL AND r.archived_at IS NULL
  AND r.organization_id = ANY($1)
ORDER BY r.title, r.id
"""

# Backlog rules key on title, so they MUST be scoped to the WA chambers /
# Legislature. Unscoped, a generic title sweeps in unrelated orgs — e.g. King
# County also has a "Senior Policy Analyst", which would have been mistyped as
# WA legislature_staff.
_BACKLOG_ROW_SQL = """
SELECT r.id, r.organization_id, r.title
FROM roles r
WHERE r.role_type_id IS NULL AND r.jurisdiction_id IS NULL AND r.archived_at IS NULL
  AND r.title = $1
  AND EXISTS (
      SELECT 1 FROM identifiers i
      JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
      WHERE i.entity_id = r.organization_id
        AND t.slug IN ('org_wa_legislature_chamber', 'org_wa_legislature')
  )
ORDER BY r.id
"""

_MERGE_PAIR_SQL = """
SELECT v.id AS variant_id, v.title AS variant_title, c.id AS canonical_id
FROM roles v
JOIN roles c
  ON c.organization_id = v.organization_id
 AND lower(c.title) = lower($2)
 AND c.archived_at IS NULL
 AND c.jurisdiction_id IS NULL
WHERE lower(v.title) = lower($1) AND v.archived_at IS NULL AND v.jurisdiction_id IS NULL
"""

_RENAME_CANDIDATES_SQL = """
SELECT r.id, r.organization_id, r.title,
       EXISTS (
           SELECT 1 FROM roles x
           WHERE x.organization_id = r.organization_id
             AND lower(x.title) = lower($2)
             AND x.archived_at IS NULL
       ) AS target_exists
FROM roles r
WHERE lower(r.title) = lower($1) AND r.archived_at IS NULL AND r.jurisdiction_id IS NULL
"""

_REPOINT_ASSIGNMENTS_SQL = "UPDATE role_assignments SET role_id = $2 WHERE role_id = $1"
_ARCHIVE_ROLE_SQL = "UPDATE roles SET archived_at = NOW() WHERE id = $1"
_RENAME_SQL = "UPDATE roles SET title = $2 WHERE id = $1"
_CLASSIFY_SQL = "UPDATE roles SET role_type_id = $2 WHERE id = $1"


async def _role_type_ids(conn: asyncpg.Connection) -> dict[str, str]:
    """Map every #266 slug this script assigns to its role_types id; hard-fail if absent."""
    needed = set(_COMMITTEE_OFFICEHOLDER_TYPES.values()) | {
        "legislature_staff",
        "chamber_leader",
        "chamber_officer",
    }
    ids = {
        row["slug"]: row["id"]
        for row in await conn.fetch(
            "SELECT slug, id FROM role_types WHERE slug = ANY($1)", list(needed)
        )
    }
    missing = needed - ids.keys()
    if missing:
        raise RuntimeError(f"missing role_types {sorted(missing)} — run apply_schema first")
    return ids


class Curation(NamedTuple):
    """Phase-1 outcome plus the pending state later phases must honour.

    In a dry run nothing is written, so the classification queries would still
    see the pre-curation titles and the not-yet-archived merge losers. Carrying
    the pending state forward makes the dry run predict execute faithfully —
    without it the report showed ``1st Vice Chair`` typed as staff (its rename to
    ``First Vice Chair`` hadn't landed) and merge losers being classified.
    """

    actions: list[Action]
    merged_ids: frozenset[str]
    renamed_titles: dict[str, str]  # role_id -> post-rename title


async def _curate_collisions(conn: asyncpg.Connection, *, execute: bool) -> Curation:
    """Merge two-spellings-of-one-office pairs and apply collision-free renames."""
    actions: list[Action] = []
    merged_ids: set[str] = set()
    renamed_titles: dict[str, str] = {}

    for variant, canonical in _TITLE_MERGES:
        for row in await conn.fetch(_MERGE_PAIR_SQL, variant, canonical):
            if execute:
                await conn.execute(_REPOINT_ASSIGNMENTS_SQL, row["variant_id"], row["canonical_id"])
                await conn.execute(_ARCHIVE_ROLE_SQL, row["variant_id"])
            merged_ids.add(row["variant_id"])
            actions.append(
                {
                    "kind": "merged",
                    "role_id": row["variant_id"],
                    "title": row["variant_title"],
                    "detail": (
                        f"assignments re-pointed to {canonical!r} ({row['canonical_id']}), archived"
                    ),
                }
            )

    for old, new in _TITLE_RENAMES.items():
        for row in await conn.fetch(_RENAME_CANDIDATES_SQL, old, new):
            if row["target_exists"]:
                actions.append(
                    {
                        "kind": "conflict",
                        "role_id": row["id"],
                        "title": row["title"],
                        "detail": f"cannot rename to {new!r} — already present on org",
                    }
                )
                continue
            if execute:
                await conn.execute(_RENAME_SQL, row["id"], new)
            renamed_titles[row["id"]] = new
            actions.append(
                {"kind": "renamed", "role_id": row["id"], "title": row["title"], "detail": new}
            )

    return Curation(actions, frozenset(merged_ids), renamed_titles)


async def _classify_committee_roles(
    conn: asyncpg.Connection, type_ids: dict[str, str], curation: Curation, *, execute: bool
) -> list[Action]:
    """Type every untyped role on a committee org: officeholder vocab, else staff."""
    actions: list[Action] = []
    for row in await conn.fetch(_UNTYPED_ON_COMMITTEE_SQL):
        if row["id"] in curation.merged_ids:
            continue  # archived by Phase 1 (execute) / will be (dry run)
        title = curation.renamed_titles.get(row["id"], row["title"])
        key = title.strip().lower()
        if key in _NON_ROLE_TITLES:
            actions.append(
                {
                    "kind": "skipped",
                    "role_id": row["id"],
                    "title": row["title"],
                    "detail": "observation artifact, not a role — left for #304",
                }
            )
            continue
        slug = _COMMITTEE_OFFICEHOLDER_TYPES.get(key, "legislature_staff")
        if execute:
            await conn.execute(_CLASSIFY_SQL, row["id"], type_ids[slug])
        actions.append(
            {"kind": "classified", "role_id": row["id"], "title": row["title"], "detail": slug}
        )
    return actions


async def _classify_staff_offices(
    conn: asyncpg.Connection, type_ids: dict[str, str], *, execute: bool
) -> list[Action]:
    """Type untyped roles on the legislative staff offices as legislature_staff."""
    actions: list[Action] = []
    for row in await conn.fetch(_UNTYPED_ON_ORGS_SQL, list(_STAFF_OFFICE_ORG_IDS)):
        if execute:
            await conn.execute(_CLASSIFY_SQL, row["id"], type_ids["legislature_staff"])
        actions.append(
            {
                "kind": "classified",
                "role_id": row["id"],
                "title": row["title"],
                "detail": "legislature_staff",
            }
        )
    return actions


async def _apply_backlog(
    conn: asyncpg.Connection, type_ids: dict[str, str], *, execute: bool
) -> list[Action]:
    """Apply the enumerated chamber-backlog rules: type, retitle, re-home, notes."""
    actions: list[Action] = []
    for rule in _BACKLOG_RULES:
        for row in await conn.fetch(_BACKLOG_ROW_SQL, rule.title):
            target_org = rule.rehome_org_id or row["organization_id"]
            target_title = rule.new_title or row["title"]
            clash = await conn.fetchval(
                "SELECT 1 FROM roles WHERE organization_id=$1 AND lower(title)=lower($2)"
                " AND id <> $3 AND archived_at IS NULL",
                target_org,
                target_title,
                row["id"],
            )
            if clash:
                actions.append(
                    {
                        "kind": "conflict",
                        "role_id": row["id"],
                        "title": row["title"],
                        "detail": f"target {target_title!r} already on org {target_org}",
                    }
                )
                continue
            if execute:
                await conn.execute(
                    "UPDATE roles SET role_type_id=$2, title=$3, organization_id=$4,"
                    " notes = COALESCE($5, notes) WHERE id=$1",
                    row["id"],
                    type_ids[rule.role_type],
                    target_title,
                    target_org,
                    rule.notes,
                )
            detail = rule.role_type
            if rule.new_title:
                detail += f", retitled {target_title!r}"
            if rule.rehome_org_id:
                detail += f", re-homed to {rule.rehome_org_id}"
            if rule.notes:
                detail += ", notes set"
            actions.append(
                {
                    "kind": "classified",
                    "role_id": row["id"],
                    "title": row["title"],
                    "detail": detail,
                }
            )
            if rule.flag_tenure:
                logger.warning(
                    "%s (%s): tenure moved to notes — set assignment dates/currency by hand (#307:"
                    " dates are never invented)",
                    row["title"],
                    row["id"],
                )
    return actions


async def classify_legislative_roles(conn: asyncpg.Connection, *, execute: bool) -> Report:
    """Run all four phases; returns every planned/applied action."""
    type_ids = await _role_type_ids(conn)
    curation = await _curate_collisions(conn, execute=execute)
    # Curation must land before classification so renamed titles map correctly.
    actions = list(curation.actions)
    actions += await _classify_committee_roles(conn, type_ids, curation, execute=execute)
    actions += await _classify_staff_offices(conn, type_ids, execute=execute)
    actions += await _apply_backlog(conn, type_ids, execute=execute)
    return Report(actions=actions)


async def run(dsn: str, *, execute: bool) -> None:
    """Connect to DATABASE_URL and classify the legacy WA legislative roles."""
    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                report = await classify_legislative_roles(conn, execute=True)
        else:
            report = await classify_legislative_roles(conn, execute=False)

        for action in report["actions"]:
            logger.info(
                "%-10s %s %-46r %s",
                action["kind"],
                action["role_id"],
                action["title"],
                action["detail"],
            )
        counts = Counter(a["kind"] for a in report["actions"])
        breakdown = ", ".join(f"{k}={n}" for k, n in sorted(counts.items())) or "nothing to do"
        verb = "Applied" if execute else "Dry run — would apply"
        logger.info("%s %d action(s): %s", verb, len(report["actions"]), breakdown)
        if not execute:
            logger.info("Pass --execute to commit")
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute", action="store_true", help="Commit changes (default is dry run)"
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute))


if __name__ == "__main__":
    main()
