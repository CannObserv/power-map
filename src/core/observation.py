"""Core observation service: identifier-based entity match or create + per-surface writers."""

import json
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from src.api.public.schemas import ObservationAcronym, ObservationOrgName, ObservationPersonName

from src.core.db import generate_id
from src.core.logging import get_logger
from src.core.normalizers.address import get_address_normalizer
from src.core.normalizers.email import EmailNormalizer
from src.core.normalizers.phone import PhoneNormalizer
from src.core.organizations import ActiveOnArchivedOrg, OrgNotFound, set_org_active
from src.core.role_title import synthesize_role_title
from src.core.types import EVENT_PLACE_PRECISIONS

logger = get_logger(__name__)

_email_normalizer = EmailNormalizer()
_phone_normalizer = PhoneNormalizer()

# Person name_types that must never be auto-promoted into the display slot (#308b).
# `deadname` is forced to visibility='legal_only' by trg_deadname_visibility, and
# mrz/romanization/reading are machine-readable renderings, not display names.
# A client may still promote one explicitly via the is_canonical hint.
NO_AUTO_CANONICAL_NAME_TYPES = frozenset({"deadname", "mrz", "romanization", "reading"})

# Display preference when a single observation carries several eligible names and
# the client gave no hint. Mirrors the name_type ordering in v_person_display_names
# (#308a) so PM promotes the same row the view would have picked. Lower sorts first;
# unlisted types fall to the end via the .get() default.
_PERSON_NAME_TYPE_PRIORITY = {
    "preferred": 1,
    "legal": 2,
    "alias": 3,
    "stage": 4,
    "religious": 5,
    "maiden": 6,
    "variant": 7,
    "former": 8,
    "initials": 9,
}

# Same ladder as SQL, derived from the dict above so the two cannot drift.
# Mirrors the CASE in v_person_display_names (#308a) — tests/core/
# test_schema_person_display_view.py asserts the view agrees with this mapping.
_NAME_TYPE_PRIORITY_SQL = (
    "CASE name_type "
    + " ".join(f"WHEN '{t}' THEN {r}" for t, r in _PERSON_NAME_TYPE_PRIORITY.items())
    + " ELSE 99 END"
)


def _name_type_rank(name_type: str) -> int:
    """Display priority of a person name_type; lower wins. Unlisted sorts last."""
    return _PERSON_NAME_TYPE_PRIORITY.get(name_type, 99)


def _person_canonical_target(names: "Sequence[ObservationPersonName]") -> int | None:
    """Index of the one name in this write that may claim the canonical slot.

    Client hint wins outright. Otherwise the highest-priority non-excluded
    name_type is chosen — ``min`` is stable, so equal-priority names fall back to
    list order without an explicit tiebreak. Returns None when nothing in the
    batch is eligible.

    Returns an **index, not a name string** (#308, CR3 #22): two entries in one
    payload can share a name string while differing in name_type, and matching by
    string promoted whichever came first in list order — letting an `mrz` or
    `deadname` row claim the display slot despite
    NO_AUTO_CANONICAL_NAME_TYPES, and inverting the priority ladder for
    same-string `legal`/`preferred` pairs.

    Exactly one name is eligible per write — promoting one per name_type slot
    would leave a person carrying several canonical rows, which
    uq_person_canonical_name permits but which reads as ambiguous display state.
    """
    hinted = next((i for i, n in enumerate(names) if n.is_canonical), None)
    if hinted is not None:
        return hinted
    eligible = [i for i, n in enumerate(names) if n.name_type not in NO_AUTO_CANONICAL_NAME_TYPES]
    if not eligible:
        return None
    return min(eligible, key=lambda i: _name_type_rank(names[i].name_type))


# Guard fragments for the person-name INSERT. Both are NOT EXISTS subqueries over
# $2 (person_id) / $4 (name_type), so neither can ever displace a canonical row.
#
# Hint path stays per-name_type: a client asserting a `preferred` name alongside
# an existing canonical `legal` one is claiming a legitimate second slot.
# Auto-promotion is person-wide: its only job is to guarantee the person displays
# at all, so it stands down whenever any public canonical already exists rather
# than adding a competing row.
_CANONICAL_GUARD_HINTED = (
    "NOT EXISTS (SELECT 1 FROM person_names"
    "            WHERE person_id = $2 AND name_type = $4 AND is_canonical = TRUE)"
)
_CANONICAL_GUARD_AUTO = (
    "NOT EXISTS (SELECT 1 FROM person_names"
    "            WHERE person_id = $2 AND is_canonical = TRUE AND visibility = 'public')"
)


def _person_name_insert_sql(canonical_expr: str) -> str:
    """Build the person_names INSERT with ``canonical_expr`` as the is_canonical value.

    Callers pass either a guarded ``($9 AND <guard>)`` expression or the literal
    ``FALSE`` (blocked-slot fallback, which also drops the $9 argument). Built in
    one place so the two forms cannot drift out of sync with their arg tuples.

    Returns ``is_canonical, visibility`` so the caller can tell whether the
    person actually *displays* — a deadname row comes back canonical but
    legal_only, which is not the same thing (#308, CR3 #21) — without spending a
    second round trip to find out.
    """
    return (
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, locale, script, sort_as,"
        "  visibility, source_key_id, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, 'public', $8,"
        f"   {canonical_expr})"
        " RETURNING is_canonical, visibility"
    )


# Heal, phase 1 (#308). Read-only, one round trip, no savepoint — a SELECT
# cannot violate a constraint, so the common "person already displays" case pays
# nothing beyond the round trip.
#
# `free_id` is the highest-priority candidate whose unique-index slot
# (person_id, name_type, COALESCE(locale,''), COALESCE(script,'')) is actually
# free — the slot test lives in the candidate filter, not after it (#308, CR3
# #16), so a blocked top-priority name no longer hides a promotable lower one.
# `blocked_id` is the best candidate that exists but cannot be promoted, used
# only to warn.
_HEAL_PERSON_SELECT_SQL = f"""
WITH promotable AS (
    SELECT n.id,
           {_NAME_TYPE_PRIORITY_SQL.replace("name_type", "n.name_type")} AS rank,
           NOT EXISTS (
               SELECT 1 FROM person_names x
               WHERE x.person_id = n.person_id
                 AND x.is_canonical = TRUE
                 AND x.name_type = n.name_type
                 AND COALESCE(x.locale, '') = COALESCE(n.locale, '')
                 AND COALESCE(x.script, '') = COALESCE(n.script, '')
           ) AS slot_free
    FROM person_names n
    WHERE n.person_id = $1
      AND n.visibility = 'public'
      AND n.is_canonical = FALSE
      AND n.name_type <> ALL($2::text[])
)
SELECT EXISTS (
           SELECT 1 FROM person_names
           WHERE person_id = $1 AND is_canonical = TRUE AND visibility = 'public'
       ) AS displays,
       (SELECT id FROM promotable WHERE slot_free ORDER BY rank, id LIMIT 1) AS free_id,
       (SELECT id FROM promotable WHERE NOT slot_free ORDER BY rank, id LIMIT 1) AS blocked_id
"""

# Heal, phase 2: only runs when phase 1 found a promotable row. Guarded again so
# a snapshot taken between the two statements cannot double-promote.
_HEAL_PERSON_UPDATE_SQL = """
UPDATE person_names SET is_canonical = TRUE
WHERE id = $1
  AND is_canonical = FALSE
  AND NOT EXISTS (
      SELECT 1 FROM person_names x
      WHERE x.person_id = person_names.person_id
        AND x.is_canonical = TRUE
        AND x.visibility = 'public'
  )
"""


async def _heal_person_canonical(conn, person_id: str) -> None:
    """Promote an existing name when the person has no public canonical (#308).

    Auto-promotion on INSERT only covers *new* name rows. A person already in the
    canonical-less state is re-observed with the same names on every sync, hits
    the exact-match dedup in ``write_names``, and would otherwise stay blank
    forever. This makes the observation path self-healing, so the #308c backfill
    stays a one-off rather than the only repair route. Also runs for observations
    that carry no names at all, so any observation touching a blank person repairs
    it.

    Picks by the same name_type priority the display view uses, tie-broken by id.
    No-op when the person already displays, or when no eligible name exists (a
    person carrying only a deadname stays deliberately blank).

    When *every* candidate's canonical slot is already held by a non-public row
    (e.g. an admin-curated `legal_only` name), PM deliberately does **not**
    displace it — demotion is a curation decision. That leaves the person
    rendering blank, so it is logged at WARNING with the person_id rather than
    passing silently.

    Split into a read-only probe plus a guarded UPDATE (#308, CR3 #15/#23). The
    probe cannot violate anything, so the steady-state path — an already-displaying
    person re-observed with names PM already has — stays at one round trip with no
    savepoint. Only an actual promotion takes the savepoint, and it needs one:
    CTE snapshot consistency does not protect against a row another session
    commits *during* execution, and without recovery that UniqueViolationError
    propagates out of write_names and aborts the whole observation, which the
    route reports as `db_constraint_violation` — discarding links, addresses,
    role assignments and events over a cosmetic display-name repair.
    """
    row = await conn.fetchrow(
        _HEAL_PERSON_SELECT_SQL,
        person_id,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
    )
    if row is None or row["displays"]:
        return

    if row["free_id"]:
        try:
            async with conn.transaction():
                await conn.execute(_HEAL_PERSON_UPDATE_SQL, row["free_id"])
            return
        except asyncpg.exceptions.UniqueViolationError:
            # Another session claimed the slot between the probe and the update.
            # Best-effort by nature — their row satisfies the goal just as well.
            logger.debug("heal_person_canonical: lost race for person=%s", person_id)
            return

    if row["blocked_id"]:
        logger.warning(
            "person %s has no public canonical name: candidate name row %s cannot be"
            " promoted because a non-public canonical already holds its"
            " (name_type, locale, script) slot — needs admin curation",
            person_id,
            row["blocked_id"],
        )


class Disposition(StrEnum):
    AUTO_ATTACHED = "auto-attached"
    NEW = "new"
    REJECTED = "rejected"


class ObservationRejected(Exception):
    """Raised by attribute writers when the observation payload fails validation."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class IdentifierConflict(Exception):
    """Raised when an additional identifier conflicts with an existing one on the entity."""

    def __init__(self, identifier_type_slug: str) -> None:
        self.identifier_type_slug = identifier_type_slug
        super().__init__(f"Identifier conflict on type {identifier_type_slug!r}")


# ---------------------------------------------------------------------------
# resolve_entity (Step 5)
# ---------------------------------------------------------------------------


async def resolve_entity(
    conn,
    identifier_type_slug: str,
    identifier_value: str,
    *,
    create_data: dict | None = None,
) -> tuple[str, str, Disposition, str | None]:
    """Find or create the entity identified by the given identifier.

    Returns (entity_id, entity_type, disposition, reason).

    disposition is:
      - AUTO_ATTACHED  if an existing identifier row was found
      - NEW            if a new entity + identifier row were created
      - REJECTED       if the identifier_type_slug is unknown, or if the entity
                       type requires create_data for NEW and none was provided

    reason is a human-readable string on REJECTED, None otherwise.
    Raises nothing — REJECTED is returned, not raised.
    """
    eit = await conn.fetchrow(
        "SELECT id, entity_type, is_internal FROM entity_identifier_types WHERE slug = $1",
        identifier_type_slug,
    )
    if eit is None:
        logger.warning("Unknown identifier_type_slug=%r", identifier_type_slug)
        return "", "", Disposition.REJECTED, f"unknown_identifier_type: {identifier_type_slug!r}"

    entity_type = eit["entity_type"]

    if eit["is_internal"]:
        # PM-native lookup: bypass identifiers table, query entity row directly.
        # Never NEW — a pm_* type cannot create entities.
        entity_id = await _lookup_entity_by_pm_id(conn, entity_type, identifier_value)
        if entity_id is None:
            logger.warning("pm-internal resolve: %s id=%r not found", entity_type, identifier_value)
            return "", "", Disposition.REJECTED, f"pm_id_not_found: {identifier_value!r}"
        return entity_id, entity_type, Disposition.AUTO_ATTACHED, None

    entity_identifier_type_id = eit["id"]
    existing = await conn.fetchrow(
        "SELECT entity_id FROM identifiers WHERE entity_identifier_type_id = $1 AND value = $2",
        entity_identifier_type_id,
        identifier_value,
    )
    if existing:
        return existing["entity_id"], entity_type, Disposition.AUTO_ATTACHED, None

    if entity_type == "jurisdiction":
        if not create_data:
            logger.warning(
                "Jurisdiction NEW disposition requires create_data; identifier_type=%r",
                identifier_type_slug,
            )
            return "", "", Disposition.REJECTED, "jurisdiction_new_requires_create_data"
        # Pre-validate type_slug outside the transaction so we can return REJECTED cleanly.
        type_row = await conn.fetchrow(
            "SELECT id FROM jurisdiction_types WHERE slug=$1", create_data["type_slug"]
        )
        if type_row is None:
            logger.warning("Unknown jurisdiction_type_slug=%r", create_data["type_slug"])
            return (
                "",
                "",
                Disposition.REJECTED,
                f"unknown_jurisdiction_type: {create_data['type_slug']!r}",
            )
        create_data = {**create_data, "type_id": type_row["id"]}

    # Resolved once per call; entity_identifier_types is append-only after apply_schema.
    jur_slug_type_id: str | None = None
    if entity_type == "jurisdiction" and identifier_type_slug != "jur_slug":
        jur_slug_eit = await conn.fetchrow(
            "SELECT id FROM entity_identifier_types WHERE slug = 'jur_slug'"
        )
        if jur_slug_eit is None:
            logger.warning(
                "jur_slug identifier type not found — slug auto-registration skipped"
                " for identifier_type=%r",
                identifier_type_slug,
            )
        else:
            jur_slug_type_id = jur_slug_eit["id"]

    try:
        async with conn.transaction():
            entity_id = await _create_entity(conn, entity_type, create_data=create_data)
            await conn.execute(
                "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                entity_id,
                entity_identifier_type_id,
                identifier_value,
            )
            if jur_slug_type_id is not None:
                await conn.execute(
                    "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                    " VALUES ($1, $2, $3, $4)",
                    generate_id(),
                    entity_id,
                    jur_slug_type_id,
                    create_data["slug"],
                )
    except asyncpg.UniqueViolationError:
        logger.warning(
            "UniqueViolation creating %s for identifier_type=%r value=%r",
            entity_type,
            identifier_type_slug,
            identifier_value,
        )
        return "", "", Disposition.REJECTED, "unique_violation"
    logger.info(
        "Created %s entity_id=%s for identifier_type=%s value=%r",
        entity_type,
        entity_id,
        identifier_type_slug,
        identifier_value,
    )
    return entity_id, entity_type, Disposition.NEW, None


_ENTITY_TABLE = {
    "organization": "organizations",
    "person": "people",
    "jurisdiction": "jurisdictions",
    "role_assignment": "role_assignments",
}


async def _lookup_entity_by_pm_id(conn, entity_type: str, entity_id: str) -> str | None:
    """Return entity_id if the row exists and is not archived, else None."""
    table = _ENTITY_TABLE.get(entity_type)
    if table is None:
        return None
    return await conn.fetchval(
        f"SELECT id FROM {table} WHERE id=$1 AND archived_at IS NULL",  # noqa: S608
        entity_id,
    )


async def _create_entity(conn, entity_type: str, *, create_data: dict | None = None) -> str:
    """Insert a minimal entity row and return its id."""
    entity_id = generate_id()
    if entity_type == "person":
        await conn.execute("INSERT INTO people (id) VALUES ($1)", entity_id)
    elif entity_type == "organization":
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", entity_id)
    elif entity_type == "jurisdiction":
        if not create_data:
            raise ValueError("jurisdiction _create_entity requires create_data")
        await conn.execute(
            "INSERT INTO jurisdictions"
            " (id, slug, name, type_id, valid_from, valid_until, notes)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            entity_id,
            create_data["slug"],
            create_data["name"],
            create_data["type_id"],
            create_data.get("valid_from"),
            create_data.get("valid_until"),
            create_data.get("notes"),
        )
    elif entity_type == "role_assignment":
        raise ValueError("Cannot create bare role_assignment entity from observation")
    else:
        raise ValueError(f"Unknown entity_type: {entity_type!r}")
    return entity_id


# ---------------------------------------------------------------------------
# Per-surface writers (Step 7)
#
# Governing principle: append-only, exact-match dedup, never overwrite.
# ---------------------------------------------------------------------------


async def write_names(
    conn,
    entity_id: str,
    entity_type: str,
    api_key_id: str,
    names: "Sequence[ObservationPersonName] | Sequence[ObservationOrgName]",
) -> None:
    """Write name claims to person_names or organization_names.

    Policy:
      - Append if no exact (entity_id, name) match
      - visibility='public' (person_names only)
      - source_key_id = api_key_id on new name rows
      - parts: write on new name row; on existing row write only if parts row absent
      - is_canonical hint: honoured only if no canonical already exists for that
        (person_id, name_type) slot (person) or the whole org; never displaces.
      - person, no hint: first-wins auto-promotion (#308b) — symmetric with the
        org branch, so a silent client still yields a displayable person.
    """
    if entity_type == "person":
        # Exactly one name per write may claim canonical; hint wins, else priority.
        canonical_target = _person_canonical_target(names)
        hint_driven = any(n.is_canonical for n in names)
        canonical_guard = _CANONICAL_GUARD_HINTED if hint_driven else _CANONICAL_GUARD_AUTO
        guarded_sql = _person_name_insert_sql(f"($9 AND {canonical_guard})")
        unpromoted_sql = _person_name_insert_sql("FALSE")
        # Tracks whether this write already claimed the canonical slot, so the
        # heal pass below can be skipped — it is a guaranteed no-op in that case,
        # and costs a round trip against a remote DB (#308, CR2 #11).
        displays = False
        for idx, n in enumerate(names):
            # Dedup on the full identity, not the bare name (#308, CR3 #22): a
            # `legal` name and an `mrz` rendering can share a string while being
            # different claims, and a name-only key silently discarded the second.
            existing = await conn.fetchrow(
                "SELECT id FROM person_names"
                " WHERE person_id=$1 AND name=$2 AND name_type=$3"
                "   AND locale IS NOT DISTINCT FROM $4"
                "   AND script IS NOT DISTINCT FROM $5",
                entity_id,
                n.name,
                n.name_type,
                n.locale,
                n.script,
            )
            if existing:
                name_id = existing["id"]
                is_new = False
            else:
                is_new = True
                name_id = generate_id()
                eligible = idx == canonical_target
                base_args = (
                    name_id,
                    entity_id,
                    n.name,
                    n.name_type,
                    n.locale,
                    n.script,
                    n.sort_as,
                    api_key_id,
                )
                try:
                    async with conn.transaction():
                        claimed = await conn.fetchrow(guarded_sql, *base_args, eligible)
                        # "Claimed a slot" is not "person displays" (#308, CR3 #21):
                        # trg_deadname_visibility rewrites a deadname row to
                        # legal_only *after* is_canonical is computed, so a hinted
                        # deadname returns is_canonical=TRUE while remaining
                        # invisible to v_person_display_names. Gating the heal on
                        # the raw flag left such people blank, with the slot
                        # sealed and no warning.
                        displays = displays or (
                            bool(claimed["is_canonical"]) and claimed["visibility"] == "public"
                        )
                        if n.parts is not None:
                            await _write_person_name_parts(conn, name_id, n.parts, is_new=True)
                except asyncpg.exceptions.UniqueViolationError:
                    # The guard is keyed (person_id, name_type) — or, on the auto
                    # path, on public canonicals only — while
                    # uq_person_canonical_name spans
                    # (person_id, name_type, locale, script) regardless of
                    # visibility. So the guard can pass while the index rejects:
                    # most often a non-public canonical already holds the slot
                    # (reachable single-threaded via admin), and under genuine
                    # concurrency a racing writer in another locale/script.
                    # Either way, land the name unpromoted rather than lose the
                    # observation. Retry runs outside the failed savepoint —
                    # reusing it would hit "current transaction is aborted".
                    async with conn.transaction():
                        await conn.execute(unpromoted_sql, *base_args)
                        if n.parts is not None:
                            await _write_person_name_parts(conn, name_id, n.parts, is_new=True)
            if n.parts is not None and not is_new:
                await _write_person_name_parts(conn, name_id, n.parts, is_new=False)
        # Names that already existed are skipped above, so a person who was
        # already canonical-less stays blank without this pass (#308, CR1).
        # Skipped only when an insert above left the person actually displaying —
        # the heal would be a no-op and costs a round trip (#308, CR2 #11).
        if not displays:
            await _heal_person_canonical(conn, entity_id)
    elif entity_type == "organization":
        # If any name carries the canonical hint, only that name is eligible for promotion.
        # Otherwise fall through to first-wins auto-promotion (NOT EXISTS guard).
        canonical_hint = next((n.name for n in names if n.is_canonical), None)
        for n in names:
            existing = await conn.fetchrow(
                "SELECT id FROM organization_names WHERE organization_id=$1 AND name=$2",
                entity_id,
                n.name,
            )
            if existing:
                continue
            # eligible=True → this name may try to claim canonical via NOT EXISTS.
            # When a hint is present, only the hinted name is eligible.
            # When no hint, every name is eligible (first insert wins via NOT EXISTS).
            eligible = (canonical_hint is None) or (n.name == canonical_hint)
            try:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO organization_names"
                        " (id, organization_id, name, name_type, source_key_id,"
                        "  effective_start, effective_end, is_canonical)"
                        " VALUES ($1, $2, $3, $4, $5, $6, $7,"
                        "   ($8 AND NOT EXISTS (SELECT 1 FROM organization_names"
                        "               WHERE organization_id = $2 AND is_canonical = TRUE)))",
                        generate_id(),
                        entity_id,
                        n.name,
                        n.name_type,
                        api_key_id,
                        n.effective_start,
                        n.effective_end,
                        eligible,
                    )
            except asyncpg.exceptions.UniqueViolationError:
                # Concurrent write promoted a different name first; insert without canonical.
                await conn.execute(
                    "INSERT INTO organization_names"
                    " (id, organization_id, name, name_type, source_key_id,"
                    "  effective_start, effective_end)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    generate_id(),
                    entity_id,
                    n.name,
                    n.name_type,
                    api_key_id,
                    n.effective_start,
                    n.effective_end,
                )
    elif entity_type == "jurisdiction":
        pass  # jurisdiction name lives on the row; no names table
    else:
        raise ValueError(f"write_names: unsupported entity_type {entity_type!r}")


async def _write_person_name_parts(conn, name_id: str, parts, *, is_new: bool) -> None:
    """Insert person_name_parts row. On new name row → write unconditionally.
    On existing row → write only if no parts row already exists (write-if-null).
    """
    if not is_new:
        existing = await conn.fetchrow(
            "SELECT person_name_id FROM person_name_parts WHERE person_name_id=$1",
            name_id,
        )
        if existing:
            return
    await conn.execute(
        "INSERT INTO person_name_parts"
        " (person_name_id, given_names, family_names, additional_names,"
        "  honorific_prefix, honorific_suffix, primary_identifier)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        name_id,
        list(parts.given_names) or None,
        list(parts.family_names) or None,
        list(parts.additional_names) or None,
        parts.honorific_prefix,
        parts.honorific_suffix,
        parts.primary_identifier,
    )


async def write_links(conn, entity_id: str, entity_type: str, links: list) -> None:
    """Write link claims. Resolves link_type_slug → id if needed.

    Dedup on (entity_type, entity_id, url, link_type_id).
    """
    for link in links:
        link_type_id = link.link_type_id
        if link_type_id is None:
            row = await conn.fetchrow(
                "SELECT id FROM link_types WHERE slug=$1", link.link_type_slug
            )
            if row is None:
                raise ObservationRejected(f"Unknown link_type_slug: {link.link_type_slug!r}")
            link_type_id = row["id"]
        existing = await conn.fetchrow(
            "SELECT id FROM links"
            " WHERE entity_type=$1 AND entity_id=$2 AND url=$3 AND link_type_id=$4",
            entity_type,
            entity_id,
            link.url,
            link_type_id,
        )
        if existing:
            continue
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, $2, $3, $4, $5)",
                generate_id(),
                entity_type,
                entity_id,
                link.url,
                link_type_id,
            )
            await _record_entity_change(conn, entity_type, entity_id)


async def _record_entity_change(conn, entity_type: str, entity_id: str) -> None:
    """Append an 'updated' outbox row; callers own any surrounding transaction."""
    await conn.execute(
        "INSERT INTO entity_changes (entity_type, entity_id, change_kind)"
        " VALUES ($1, $2, 'updated')",
        entity_type,
        entity_id,
    )


async def _null_fill_metadata(
    conn,
    entity_type: str,
    entity_id: str,
    table: str,
    col: str,
    pk_val: str,
    value: str,
    record_change: bool = True,
) -> None:
    """Fill col in table where pk_val row has NULL, atomically with an entity_changes row.

    table/col are caller-controlled string constants, not user input.
    Pass record_change=False for tables with a touch-parent trigger
    (entity_addresses) — the trigger already emits the outbox row.
    """
    async with conn.transaction():
        updated = await conn.fetchval(
            f"UPDATE {table} SET {col}=$1 WHERE id=$2 AND {col} IS NULL RETURNING id",
            value,
            pk_val,
        )
        if updated and record_change:
            await _record_entity_change(conn, entity_type, entity_id)


async def write_contact_methods(
    conn, entity_id: str, entity_type: str, contact_methods: list
) -> None:
    """Normalise and write contact method claims.

    Raises ObservationRejected on bad format. Dedup on
    (entity_type, entity_id, contact_type, value) after normalisation.
    """
    for cm in contact_methods:
        try:
            if cm.contact_type == "email":
                normalized = _email_normalizer.normalize(cm.value).value
            elif cm.contact_type == "phone":
                normalized = _phone_normalizer.normalize(cm.value).value
            else:
                raise ObservationRejected(f"Unsupported contact_type: {cm.contact_type!r}")
        except ValueError as exc:
            raise ObservationRejected(str(exc)) from exc
        if normalized is None:
            raise ObservationRejected(f"Empty contact value for type {cm.contact_type!r}")
        existing = await conn.fetchrow(
            "SELECT id FROM contact_methods"
            " WHERE entity_type=$1 AND entity_id=$2 AND contact_type=$3 AND value=$4",
            entity_type,
            entity_id,
            cm.contact_type,
            normalized,
        )
        if existing:
            if cm.display_label:
                await _null_fill_metadata(
                    conn,
                    entity_type,
                    entity_id,
                    "contact_methods",
                    "display_label",
                    existing["id"],
                    cm.display_label,
                )
            continue
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO contact_methods"
                " (id, entity_type, entity_id, contact_type, value, display_label)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                generate_id(),
                entity_type,
                entity_id,
                cm.contact_type,
                normalized,
                cm.display_label,
            )
            await _record_entity_change(conn, entity_type, entity_id)


async def write_addresses(conn, entity_id: str, entity_type: str, addresses: list) -> None:
    """Write address claims via the address normalizer.

    Dedup mirrors the DB unique key on ``entity_addresses`` — the normalized form
    plus the validity window ``(valid_from, valid_until)`` (#256):

    - **Dated claim** (either window bound supplied): strict window equality via
      ``IS NOT DISTINCT FROM``. A new window of an address the entity already has
      is history, not a duplicate — a fresh link is written, reusing the existing
      ``addresses`` row rather than inserting a per-window duplicate.
    - **Dateless claim** (both bounds NULL): matches *any* existing row regardless
      of window (unchanged behavior). Admin end-dating stays authoritative — a
      dateless re-observation never resurrects a closed window
      (docs/CONVENTIONS.md §"Address validity windows (#181)", #256 decision).

    Raises ObservationRejected on normalizer failure.
    """
    normalizer = get_address_normalizer()
    for addr in addresses:
        try:
            result = await normalizer.normalize(addr.raw_input)
        except Exception as exc:
            raise ObservationRejected(f"Address normalization failed: {exc}") from exc
        if result.skipped:
            raise ObservationRejected(
                f"Address skipped by normalizer (unrecognised format): {addr.raw_input!r}"
            )
        if result.value is None:
            raise ObservationRejected(
                f"Address normalisation returned no result for: {addr.raw_input!r}"
            )
        v = result.value
        # Dedup key: standardized form if present, else raw_input
        dedup_form = v.get("standardized") or v.get("raw_input") or addr.raw_input
        dated = addr.valid_from is not None or addr.valid_until is not None
        # Dated claims match on the exact window; dateless claims match any window.
        window_clause = (
            "   AND ea.valid_from IS NOT DISTINCT FROM $5"
            "   AND ea.valid_until IS NOT DISTINCT FROM $6"
            if dated
            else ""
        )
        params = [entity_type, entity_id, addr.address_type, dedup_form]
        if dated:
            params += [addr.valid_from, addr.valid_until]
        existing = await conn.fetchrow(
            "SELECT ea.id FROM entity_addresses ea"
            " JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_type=$1 AND ea.entity_id=$2 AND ea.address_type=$3"
            "   AND COALESCE(a.standardized, a.raw_input) = $4" + window_clause,
            *params,
        )
        if existing:
            if addr.display_name:
                # trg_touch_entity_on_address_change emits the outbox row (#181)
                await _null_fill_metadata(
                    conn,
                    entity_type,
                    entity_id,
                    "entity_addresses",
                    "display_name",
                    existing["id"],
                    addr.display_name,
                    record_change=False,
                )
            continue
        # No exact (form + window) match. Reuse an existing addresses row for the
        # same entity + type + form in any window so a new window does not spawn a
        # duplicate physical address; only mint a fresh row when the form is new.
        reuse = await conn.fetchrow(
            "SELECT ea.address_id FROM entity_addresses ea"
            " JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_type=$1 AND ea.entity_id=$2 AND ea.address_type=$3"
            "   AND COALESCE(a.standardized, a.raw_input) = $4"
            " ORDER BY ea.created_at, ea.id"
            " LIMIT 1",
            entity_type,
            entity_id,
            addr.address_type,
            dedup_form,
        )
        components_val = v.get("components")
        components_str = json.dumps(components_val) if components_val else None
        async with conn.transaction():
            if reuse:
                aid = reuse["address_id"]
            else:
                aid = generate_id()
                await conn.execute(
                    "INSERT INTO addresses"
                    " (id, raw_input, address_line_1, address_line_2, city, region,"
                    "  postal_code, country, standardized, latitude, longitude, components)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                    aid,
                    v.get("raw_input") or addr.raw_input,
                    v.get("address_line_1"),
                    v.get("address_line_2"),
                    v.get("city"),
                    v.get("region"),
                    v.get("postal_code"),
                    v.get("country") or "US",
                    v.get("standardized"),
                    v.get("latitude"),
                    v.get("longitude"),
                    components_str,
                )
            await conn.execute(
                "INSERT INTO entity_addresses"
                " (id, entity_type, entity_id, address_id, address_type, display_name,"
                "  valid_from, valid_until)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                generate_id(),
                entity_type,
                entity_id,
                aid,
                addr.address_type,
                addr.display_name,
                addr.valid_from,
                addr.valid_until,
            )
            # trg_touch_entity_on_address_change emits the outbox row (#181)


async def write_org_acronyms(
    conn, organization_id: str, acronyms: "list[ObservationAcronym]"
) -> None:
    """Append acronyms. Dedup on exact string. Auto-promotes first to canonical.

    If any entry carries is_canonical=True, only that entry is eligible for
    promotion; all others land as non-canonical.  When no hint is given, the
    first new acronym wins (NOT EXISTS guard), matching prior behaviour.
    """
    canonical_hint = next((a.acronym for a in acronyms if a.is_canonical), None)
    for a in acronyms:
        existing = await conn.fetchrow(
            "SELECT id FROM organization_acronyms WHERE organization_id=$1 AND acronym=$2",
            organization_id,
            a.acronym,
        )
        if existing:
            continue
        eligible = (canonical_hint is None) or (a.acronym == canonical_hint)
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                    " VALUES ($1, $2, $3,"
                    "   ($4 AND NOT EXISTS (SELECT 1 FROM organization_acronyms"
                    "               WHERE organization_id = $2 AND is_canonical = TRUE)))",
                    generate_id(),
                    organization_id,
                    a.acronym,
                    eligible,
                )
        except asyncpg.exceptions.UniqueViolationError:
            # Concurrent write promoted a different acronym first; insert without canonical.
            await conn.execute(
                "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                " VALUES ($1, $2, $3, FALSE)",
                generate_id(),
                organization_id,
                a.acronym,
            )


async def resolve_role(
    conn,
    organization_id: str,
    title: str | None,
    *,
    notes: str | None = None,
    established_on: date | None = None,
    abolished_on: date | None = None,
    role_type: str | None = None,
    jurisdiction_id: str | None = None,
    qualifier: str | None = None,
) -> tuple[str, Disposition, str | None]:
    """Match or create a role.

    Roles without a jurisdiction (jurisdiction_id is None) match by
    (organization_id, lower(title)). Roles with a jurisdiction (jurisdiction_id
    set) match by (organization_id, role_type, jurisdiction_id, qualifier), so
    distinct roles sharing a title — e.g. the two WA House positions in a
    district — never collapse into one another, and a title-only observation
    never glues onto a role with a jurisdiction.

    ``role_type`` is a ``role_types`` slug, resolved and validated here — the
    single resolution point for both the observation endpoint and direct
    callers.

    ``title`` is optional for a role with a jurisdiction: on create PM
    synthesizes the canonical title from the structural tuple via
    ``src.core.role_title`` (#267) and **prefers it over any supplied title**, so
    an upstream observer never drifts PM's curated form. A supplied title is used
    only as a fallback when the title cannot be synthesized (unknown role_type /
    non-``usa-wa-ld`` jurisdiction); a jurisdictional role with neither is
    REJECTED (``role_title_unavailable``). A role without a jurisdiction requires
    a title — it is the match key — else REJECTED (``title_required``).

    Jurisdiction history: a superseded/redistricted district row stays
    ``archived_at IS NULL`` (supersession is tracked via ``superseded_at`` /
    ``valid_until`` / lineage edges, not soft-delete), so it remains a valid
    reference — historical roles can be created against the district that was in
    effect. Only a truly archived (soft-deleted) district is rejected.

    Returns (role_id, disposition, reason).
    disposition is AUTO_ATTACHED if an active (non-archived) match is found,
    NEW if created, REJECTED if a referenced entity is missing/archived, a role
    with a jurisdiction omits its role type, or a jurisdictional observation of a
    ``requires_qualifier`` office omits the qualifier (``qualifier_required``,
    #273). reason is a human-readable string on REJECTED, None otherwise.
    """
    org_exists = await conn.fetchval(
        "SELECT 1 FROM organizations WHERE id=$1 AND archived_at IS NULL", organization_id
    )
    if not org_exists:
        logger.warning("resolve_role: unknown organization_id=%r", organization_id)
        return "", Disposition.REJECTED, f"org_not_found: {organization_id!r}"

    role_type_id: str | None = None
    requires_qualifier = False
    if role_type is not None:
        rt = await conn.fetchrow(
            "SELECT id, requires_qualifier FROM role_types WHERE slug=$1", role_type
        )
        if rt is None:
            return "", Disposition.REJECTED, f"role_type_not_found: {role_type!r}"
        role_type_id = rt["id"]
        requires_qualifier = rt["requires_qualifier"]

    if jurisdiction_id is not None:
        jur = await conn.fetchrow(
            "SELECT archived_at, slug FROM jurisdictions WHERE id=$1", jurisdiction_id
        )
        if jur is None:
            return "", Disposition.REJECTED, f"jurisdiction_not_found: {jurisdiction_id!r}"
        if jur["archived_at"] is not None:
            return "", Disposition.REJECTED, f"jurisdiction_archived: {jurisdiction_id!r}"
        if role_type_id is None:
            return "", Disposition.REJECTED, "role_type_required_for_jurisdiction"
        # A per-position office (e.g. a WA House seat) needs a qualifier; without
        # one, a create would mint a spurious positionless seat (#267/#273).
        # Reject before match/create so the omission is loud, not silently minted.
        # Treat an empty/whitespace qualifier as missing (the API normalizes it to
        # None, but a direct caller might not) so it can't slip past as a distinct
        # blank-qualifier seat.
        if requires_qualifier and not (qualifier or "").strip():
            return "", Disposition.REJECTED, f"qualifier_required: role_type={role_type!r}"

    # A qualifier only disambiguates roles with a jurisdiction; drop it for roles
    # without one so it never persists without a jurisdiction. The
    # chk_role_qualifier_needs_jurisdiction CHECK backstops other insert paths.
    if jurisdiction_id is None:
        qualifier = None

    # NOTE: title-mode matching below keys on (org, lower(title)) and ignores
    # role_type_id — by design. Typed jurisdiction-less roles now exist (the
    # `member` classifier, #269): role_type_id is persisted on create (below), so
    # the classifier lands on the row and aggregates cleanly, but it is *not* a
    # match key here — (org, title) stays the sole key, so a producer's emitter
    # needs no change. One consequence: a pre-existing *untyped* role with the
    # same (org, title) AUTO_ATTACHes and is left untyped (role_type_id not
    # upgraded in place). Benign for greenfield ingest (fresh orgs); revisit with
    # an upgrade-on-match if untyped duplicates of a typed role need reconciling.

    if jurisdiction_id is not None:
        existing = await conn.fetchrow(
            "SELECT id FROM roles WHERE organization_id=$1"
            " AND role_type_id IS NOT DISTINCT FROM $2"
            " AND jurisdiction_id=$3"
            " AND qualifier IS NOT DISTINCT FROM $4"
            " AND archived_at IS NULL",
            organization_id,
            role_type_id,
            jurisdiction_id,
            qualifier,
        )
    else:
        existing = await conn.fetchrow(
            "SELECT id FROM roles WHERE organization_id=$1 AND lower(title)=lower($2)"
            " AND jurisdiction_id IS NULL AND archived_at IS NULL",
            organization_id,
            title,
        )
    if existing:
        return existing["id"], Disposition.AUTO_ATTACHED, None

    if jurisdiction_id is not None:
        # Jurisdictional-role create: PM curates the canonical title from the
        # structural tuple (#267) and prefers it over any supplied title, so an
        # upstream observer can never drift PM's form. A supplied title is used
        # only as a fallback when it can't be synthesized (unknown role_type /
        # non-usa-wa-ld jurisdiction); if neither is available, reject.
        title = synthesize_role_title(role_type, jur["slug"], qualifier) or title
        if not title:
            return (
                "",
                Disposition.REJECTED,
                f"role_title_unavailable: role_type={role_type!r} jurisdiction={jur['slug']!r}",
            )
    elif not title:
        # Role without a jurisdiction: title is the match key and is required.
        return "", Disposition.REJECTED, "title_required"

    role_id = generate_id()
    await conn.execute(
        "INSERT INTO roles"
        " (id, organization_id, title, notes, established_on, abolished_on,"
        "  role_type_id, jurisdiction_id, qualifier)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        role_id,
        organization_id,
        title,
        notes,
        established_on,
        abolished_on,
        role_type_id,
        jurisdiction_id,
        qualifier,
    )
    logger.info(
        "Created role id=%s org=%s title=%r jurisdiction=%s qualifier=%r",
        role_id,
        organization_id,
        title,
        jurisdiction_id,
        qualifier,
    )
    return role_id, Disposition.NEW, None


async def write_role_assignments(conn, person_id: str, role_assignments: list) -> None:
    """Append role assignments. No-op if open (no end_date) assignment exists for same role."""
    for ra in role_assignments:
        open_existing = await conn.fetchrow(
            "SELECT id FROM role_assignments"
            " WHERE person_id=$1 AND role_id=$2 AND end_date IS NULL"
            "   AND archived_at IS NULL",
            person_id,
            ra.role_id,
        )
        if open_existing:
            continue
        start_date = date.fromisoformat(ra.start_date) if ra.start_date else None
        end_date = date.fromisoformat(ra.end_date) if ra.end_date else None
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, start_date, end_date)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            person_id,
            ra.role_id,
            start_date,
            end_date,
        )


async def write_org_parent(conn, organization_id: str, parent_id: str) -> None:
    """Set organizations.parent_id if currently NULL (write-if-null)."""
    await conn.execute(
        "UPDATE organizations SET parent_id=$1 WHERE id=$2 AND parent_id IS NULL",
        parent_id,
        organization_id,
    )


async def write_org_active(conn, organization_id: str, active: bool) -> None:
    """Set an organization's active flag from an observation (#240).

    The active axis is orthogonal to archived_at, but an archived org is not a
    valid observation target: archiving is an admin lifecycle gate, so asserting
    active on an archived row is treated as a malformed observation and rejected.

    Delegates the archived + no-op guards to the shared core helper
    ``set_org_active`` (#241), mapping its rejections onto the observation-domain
    ``ObservationRejected``. The caller (resolve_entity) guarantees the org
    exists and runs inside the observation transaction, so the helper's
    ``FOR UPDATE`` lock is held until commit; the OrgNotFound mapping is a
    defensive belt against a concurrent hard-delete in that window.
    """
    try:
        await set_org_active(conn, organization_id, active)
    except ActiveOnArchivedOrg as exc:
        raise ObservationRejected("active_on_archived_org") from exc
    except OrgNotFound as exc:
        raise ObservationRejected("org_not_found") from exc


async def write_org_jurisdiction_affiliations(
    conn, organization_id: str, affiliations: list
) -> None:
    """Insert org-jurisdiction affiliation rows (idempotent).

    Raises ObservationRejected if an affiliation_type_slug is not found.
    FK violations on jurisdiction_id propagate as asyncpg.ForeignKeyViolationError.
    """
    if not affiliations:
        return
    for aff in affiliations:
        type_id = await conn.fetchval(
            "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug = $1",
            aff.affiliation_type_slug,
        )
        if type_id is None:
            raise ObservationRejected(
                f"Unknown affiliation_type_slug: {aff.affiliation_type_slug!r}"
            )
        await conn.execute(
            """
            INSERT INTO organization_jurisdiction_affiliations
                (id, organization_id, jurisdiction_id, affiliation_type_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (organization_id, jurisdiction_id, affiliation_type_id) DO NOTHING
            """,
            generate_id(),
            organization_id,
            aff.jurisdiction_id,
            type_id,
        )


async def write_pronouns(conn, person_id: str, pronouns: str) -> None:
    """Set people.personal_pronouns if currently NULL (write-if-null)."""
    await conn.execute(
        "UPDATE people SET personal_pronouns=$1 WHERE id=$2 AND personal_pronouns IS NULL",
        pronouns,
        person_id,
    )


async def lookup_org_parent_by_name(conn, name: str) -> str:
    """Return id of the single active org whose canonical name matches.

    Raises ObservationRejected on zero or multiple matches.
    """
    rows = await conn.fetch(
        """
        SELECT o.id FROM organizations o
        JOIN organization_names n ON n.organization_id = o.id
        WHERE n.name = $1 AND n.is_canonical = TRUE AND o.archived_at IS NULL
        """,
        name,
    )
    if len(rows) != 1:
        raise ObservationRejected(
            f"Org parent name lookup returned {len(rows)} matches (expected 1)"
        )
    return rows[0]["id"]


async def lookup_org_parent_by_acronym(conn, acronym: str) -> str:
    """Return id of the single active org whose canonical acronym matches.

    Raises ObservationRejected on zero or multiple matches.
    """
    rows = await conn.fetch(
        """
        SELECT o.id FROM organizations o
        JOIN organization_acronyms a ON a.organization_id = o.id
        WHERE a.acronym = $1 AND a.is_canonical = TRUE AND o.archived_at IS NULL
        """,
        acronym,
    )
    if len(rows) != 1:
        raise ObservationRejected(
            f"Org parent acronym lookup returned {len(rows)} matches (expected 1)"
        )
    return rows[0]["id"]


async def write_additional_identifiers(conn, entity_id: str, additional_identifiers: list) -> None:
    """Write additional identifier claims.

    Each item must expose ``.identifier_type_slug`` and ``.identifier_value``.

    Policy:
      - Same type + same value on entity → no-op
      - Same type + different value on entity → raise IdentifierConflict
      - Unknown type slug → raise ObservationRejected
      - New type → insert
    """
    for item in additional_identifiers:
        slug = item.identifier_type_slug
        value = item.identifier_value
        eit = await conn.fetchrow(
            "SELECT id, is_internal FROM entity_identifier_types WHERE slug=$1", slug
        )
        if eit is None:
            raise ObservationRejected(f"Unknown identifier_type_slug: {slug!r}")
        if eit["is_internal"]:
            raise ObservationRejected(
                f"Internal identifier type {slug!r} cannot be assigned via observations"
            )
        eit_id = eit["id"]
        existing = await conn.fetchrow(
            "SELECT value FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
            entity_id,
            eit_id,
        )
        if existing is not None:
            if existing["value"] == value:
                continue
            raise IdentifierConflict(slug)
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            entity_id,
            eit_id,
            value,
        )


async def write_entity_events(
    conn: asyncpg.Connection,
    entity_id: str,
    entity_type: str,
    key_id: str | None,
    events: list,
) -> None:
    """Write entity event claims. Append-only with application-layer dedup.

    Dedup key: (event_type_id, event_year, event_month, event_day, event_hour,
    event_minute, event_second, linked_entity_id) — NULLs treated as equal.
    Validation:
    - applies_to mismatch → ObservationRejected
    - requires_year with no event_year → ObservationRejected
    - requires_linked_entity with no linked_entity_id → ObservationRejected
    """
    for ev in events:
        # Resolve slug → id (single query on slug path)
        event_type_id = ev.event_type_id
        if event_type_id is None:
            etype = await conn.fetchrow(
                "SELECT id, applies_to, requires_year, requires_linked_entity"
                " FROM entity_event_types WHERE slug=$1",
                ev.event_type_slug,
            )
            if etype is None:
                raise ObservationRejected(f"Unknown event_type_slug: {ev.event_type_slug!r}")
            event_type_id = etype["id"]
        else:
            etype = await conn.fetchrow(
                "SELECT applies_to, requires_year, requires_linked_entity"
                " FROM entity_event_types WHERE id=$1",
                event_type_id,
            )
            if etype is None:
                raise ObservationRejected(f"Unknown event_type_id: {event_type_id!r}")

        # applies_to check
        if etype["applies_to"] != "both" and etype["applies_to"] != entity_type:
            raise ObservationRejected(
                f"Event type {event_type_id!r} does not apply to {entity_type!r}"
            )

        # required field validation
        if etype["requires_year"] and ev.event_year is None:
            raise ObservationRejected(f"Event type {event_type_id!r} requires event_year")
        if etype["requires_linked_entity"] and not ev.linked_entity_id:
            raise ObservationRejected(f"Event type {event_type_id!r} requires linked_entity_id")

        # Validate event_place_address_id when provided
        place_addr_id = ev.event_place_address_id
        if place_addr_id:
            addr_row = await conn.fetchrow(
                "SELECT id, precision FROM addresses WHERE id=$1", place_addr_id
            )
            if addr_row is None:
                raise ObservationRejected(f"event_place_address_id {place_addr_id!r} not found")
            # NULL precision = pre-geocoding / historical record — allowed intentionally.
            if (
                addr_row["precision"] is not None
                and addr_row["precision"] not in EVENT_PLACE_PRECISIONS
            ):  # noqa: E501
                raise ObservationRejected(
                    f"event_place_address_id {place_addr_id!r} has precision "
                    f"'{addr_row['precision']}' — city, postal, or street required"
                )

        # Dedup: same event type + same partial date + same linked_entity_id = skip
        existing = await conn.fetchrow(
            """SELECT id FROM entity_events
               WHERE entity_id = $1
                 AND entity_type = $2
                 AND event_type_id = $3
                 AND event_year IS NOT DISTINCT FROM $4
                 AND event_month IS NOT DISTINCT FROM $5
                 AND event_day IS NOT DISTINCT FROM $6
                 AND event_hour IS NOT DISTINCT FROM $7
                 AND event_minute IS NOT DISTINCT FROM $8
                 AND event_second IS NOT DISTINCT FROM $9
                 AND linked_entity_id IS NOT DISTINCT FROM $10""",
            entity_id,
            entity_type,
            event_type_id,
            ev.event_year,
            ev.event_month,
            ev.event_day,
            ev.event_hour,
            ev.event_minute,
            ev.event_second,
            ev.linked_entity_id,
        )
        if existing:
            continue

        await conn.execute(
            """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                event_year, event_month, event_day, event_hour, event_minute, event_second,
                event_place_text, event_place_address_id,
                linked_entity_type, linked_entity_id,
                notes, visibility, source_key_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)""",
            generate_id(),
            entity_type,
            entity_id,
            event_type_id,
            ev.event_year,
            ev.event_month,
            ev.event_day,
            ev.event_hour,
            ev.event_minute,
            ev.event_second,
            ev.event_place_text,
            place_addr_id,
            ev.linked_entity_type,
            ev.linked_entity_id,
            ev.notes,
            ev.visibility,
            key_id,
        )


async def resolve_assignment(
    conn,
    person_id: str,
    role_id: str,
    start_date: date | None,
    *,
    end_date: date | None = None,
    is_current: bool = False,
    notes: str | None = None,
) -> tuple[str, Disposition, str | None]:
    """Match or create a role assignment by (person_id, role_id, start_date).

    Returns (assignment_id, disposition, reason).
    disposition is AUTO_ATTACHED if an active (non-archived) match is found,
    NEW if created, REJECTED if person_id or role_id does not exist.
    reason is a human-readable string on REJECTED, None otherwise.
    """
    person_exists = await conn.fetchval(
        "SELECT 1 FROM people WHERE id=$1 AND archived_at IS NULL", person_id
    )
    if not person_exists:
        logger.warning("resolve_assignment: unknown person_id=%r", person_id)
        return "", Disposition.REJECTED, f"person_not_found: {person_id!r}"

    role_exists = await conn.fetchval(
        "SELECT 1 FROM roles WHERE id=$1 AND archived_at IS NULL", role_id
    )
    if not role_exists:
        logger.warning("resolve_assignment: unknown role_id=%r", role_id)
        return "", Disposition.REJECTED, f"role_not_found: {role_id!r}"

    existing = await conn.fetchrow(
        "SELECT id FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND start_date IS NOT DISTINCT FROM $3"
        "   AND archived_at IS NULL",
        person_id,
        role_id,
        start_date,
    )
    if existing:
        return existing["id"], Disposition.AUTO_ATTACHED, None

    assignment_id = generate_id()
    try:
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, start_date, end_date, is_current, notes)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7)",
            assignment_id,
            person_id,
            role_id,
            start_date,
            end_date,
            is_current,
            notes,
        )
    except asyncpg.UniqueViolationError:
        # Callers may run this inside an ambient transaction (e.g. the assignment
        # observation handler). Do NOT issue further SQL after this point — the
        # failed INSERT has aborted the transaction, so the caller must roll back;
        # any query here would raise InFailedSQLTransaction instead of rejecting.
        logger.warning(
            "UniqueViolation creating role_assignment person=%r role=%r start=%r",
            person_id,
            role_id,
            start_date,
        )
        return "", Disposition.REJECTED, "unique_violation"

    logger.info(
        "Created role_assignment id=%s person=%s role=%s start=%s",
        assignment_id,
        person_id,
        role_id,
        start_date,
    )
    return assignment_id, Disposition.NEW, None


async def backfill_assignment_dates(
    conn, assignment_id: str, start_date: date | None, end_date: date | None
) -> None:
    """Backfill undated bounds onto an existing tenure in place (NULL → dated, #289).

    Out-of-band from observation match-or-create: dates an existing tenure by id
    without minting a new row. Promotes ``start_date`` and/or ``end_date`` from
    NULL to the supplied value; a supplied value equal to the current one is a
    no-op. **Must be called inside the caller's transaction** so a rejection rolls
    the whole observation back and nothing is half-written.

    Raises ``ObservationRejected`` (the handler maps it to a ``rejected``
    response) when:

    - the addressed row is archived or gone (``assignment_not_found``) — a
      defense-in-depth guard; the pm_assignment_id resolver already filters
      archived rows before this is reached;
    - a supplied bound differs from a non-NULL value already on the row
      (``start_date_conflict`` / ``end_date_conflict``);
    - promoting ``start_date`` collides with a sibling tenure sharing
      (person, role, start_date) (``start_date_conflict``).

    ``is_current`` is intentionally not backfillable — its ``False`` default is
    indistinguishable from "omitted". An ``end_date`` that contradicts an
    ``is_current`` row surfaces as a DB check violation the handler reports as
    ``db_constraint_violation``.
    """
    if start_date is None and end_date is None:
        return

    row = await conn.fetchrow(
        "SELECT start_date, end_date FROM role_assignments WHERE id=$1 AND archived_at IS NULL",
        assignment_id,
    )
    if row is None:
        raise ObservationRejected("assignment_not_found")

    for field, provided, current in (
        ("start_date", start_date, row["start_date"]),
        ("end_date", end_date, row["end_date"]),
    ):
        if provided is not None and current is not None and provided != current:
            logger.warning(
                "backfill %s conflict assignment=%s current=%s requested=%s",
                field,
                assignment_id,
                current,
                provided,
            )
            raise ObservationRejected(f"{field}_conflict")

    new_start = start_date if start_date is not None else row["start_date"]
    new_end = end_date if end_date is not None else row["end_date"]
    if new_start == row["start_date"] and new_end == row["end_date"]:
        return  # idempotent — bounds already as supplied

    try:
        await conn.execute(
            "UPDATE role_assignments SET start_date=$2, end_date=$3"
            " WHERE id=$1 AND archived_at IS NULL",
            assignment_id,
            new_start,
            new_end,
        )
    except asyncpg.UniqueViolationError as exc:
        logger.warning(
            "backfill start_date collides with sibling tenure assignment=%s start=%s",
            assignment_id,
            new_start,
        )
        raise ObservationRejected("start_date_conflict") from exc

    logger.info(
        "Backfilled role_assignment id=%s start_date=%s end_date=%s",
        assignment_id,
        new_start,
        new_end,
    )
