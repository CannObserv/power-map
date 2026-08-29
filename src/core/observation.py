"""Core observation service: identifier-based entity match or create + per-surface writers."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
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

# Subset that can never be canonical *at all*, hint or not: trg_deadname_visibility
# forces these to visibility='legal_only', which chk_person_canonical_is_public
# then rejects (#308). A client hint on one is ignored rather than allowed to
# raise CheckViolationError and fail the entire observation.
NEVER_CANONICAL_NAME_TYPES = frozenset({"deadname"})

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


def name_type_priority_sql(col: str = "name_type") -> str:
    """Render the display-priority ladder as a SQL CASE over *col*.

    Derived from `_PERSON_NAME_TYPE_PRIORITY` so the Python and SQL orderings
    cannot drift. `col` is a caller-supplied column reference (e.g. `n.name_type`
    when the query aliases person_names) — parameterised rather than patched in
    by the caller with `.replace("name_type", ...)`, which silently corrupted the
    ladder as soon as a second occurrence or a matching literal appeared (CR4 #37).

    Never interpolate untrusted input: `col` is SQL, not a bind parameter.
    """
    ladder = " ".join(f"WHEN '{t}' THEN {r}" for t, r in _PERSON_NAME_TYPE_PRIORITY.items())
    return f"CASE {col} {ladder} ELSE 99 END"


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
    hinted = next(
        (
            i
            for i, n in enumerate(names)
            if n.is_canonical and n.name_type not in NEVER_CANONICAL_NAME_TYPES
        ),
        None,
    )
    if hinted is not None:
        return hinted
    eligible = [i for i, n in enumerate(names) if n.name_type not in NO_AUTO_CANONICAL_NAME_TYPES]
    if not eligible:
        return None
    return min(eligible, key=lambda i: _name_type_rank(names[i].name_type))


# Guard for the person-name INSERT. uq_person_canonical_name is keyed on
# (person_id) alone (#308), so there is exactly one slot per person and the
# hinted and auto paths contend for the same one — a single guard serves both.
# It never displaces: an existing canonical wins, whatever this write asserts.
# chk_person_canonical_is_public guarantees a canonical row is visible, so no
# visibility term is needed here.
_CANONICAL_GUARD = (
    "NOT EXISTS (SELECT 1 FROM person_names"
    "            WHERE person_id = $2 AND is_canonical = TRUE)"
)


def _person_name_insert_sql(canonical_expr: str) -> str:
    """Build the person_names INSERT with ``canonical_expr`` as the is_canonical value.

    Callers pass either a guarded ``($9 AND <guard>)`` expression or the literal
    ``FALSE`` (blocked-slot fallback, which also drops the $9 argument). Built in
    one place so the two forms cannot drift out of sync with their arg tuples.

    Returns ``is_canonical`` so the caller knows whether this write left the
    person displaying, without a second round trip. Under
    chk_person_canonical_is_public a canonical row is always visible, so the flag
    alone is a sufficient answer — before that constraint it was not, because a
    hinted deadname came back canonical but legal_only.
    """
    return (
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, locale, script, sort_as,"
        "  visibility, source_key_id, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, 'public', $8,"
        f"   {canonical_expr})"
        " RETURNING is_canonical"
    )


# Heal, phase 1 (#308): one probe answering both "does this person display?"
# and "what would we promote if not?".
#
# There is no "blocked candidate" case any more (#308 Option A): a canonical row
# is always public by CHECK, so the only thing that can hold the slot is a row
# that already makes the person display — in which case there is nothing to heal.
# The slot-contention machinery this used to carry, and the WARNING it emitted,
# are gone with it.
_HEAL_PERSON_SELECT_SQL = f"""
SELECT EXISTS (
           SELECT 1 FROM person_names
           WHERE person_id = $1 AND is_canonical = TRUE
       ) AS displays,
       (
           SELECT id FROM person_names
           WHERE person_id = $1
             AND visibility = 'public'
             AND is_canonical = FALSE
             AND name_type <> ALL($2::text[])
           ORDER BY {name_type_priority_sql()}, id
           LIMIT 1
       ) AS candidate_id
"""

# Heal, phase 2: only runs when phase 1 found a candidate. Guarded again so a
# commit landing between the two statements cannot produce a second canonical.
_HEAL_PERSON_UPDATE_SQL = """
UPDATE person_names SET is_canonical = TRUE
WHERE id = $1
  AND is_canonical = FALSE
  AND NOT EXISTS (
      SELECT 1 FROM person_names x
      WHERE x.person_id = person_names.person_id
        AND x.is_canonical = TRUE
  )
"""


async def heal_person_canonical(conn, person_id: str) -> None:
    """Promote an existing name when the person has no public canonical (#308).

    Auto-promotion on INSERT only covers *new* name rows. A person already in the
    canonical-less state is re-observed with the same names on every sync, hits
    the exact-match dedup in ``write_names``, and would otherwise stay blank
    forever. This makes the observation path self-healing, so the #308c backfill
    stays a one-off rather than the only repair route. Also runs for observations
    that carry no names at all, so any observation touching a blank person repairs
    it.

    Picks by ``_PERSON_NAME_TYPE_PRIORITY``, tie-broken by id — the same order
    the backfill uses, so both repair paths choose the same row. No-op when the
    person already displays, or when no eligible name exists (a person carrying
    only a deadname stays deliberately blank).

    The whole body — probe *and* guarded UPDATE — runs inside one savepoint
    (CR6 #55). The contract is "the heal never aborts its caller", and a failed
    SELECT poisons the enclosing transaction exactly like a failed UPDATE does,
    so swallowing a probe error without a savepoint would leave the caller
    hitting InFailedSQLTransactionError on its next statement. Earlier rounds
    kept the probe outside to save the SAVEPOINT/RELEASE round trips on the
    steady-state path (CR3 #15/#23); that priced ~25ms of latency above a hole
    in the contract, which is the wrong way round. The UPDATE re-checks the
    guard because a concurrent commit between the two statements can still
    collide on uq_person_canonical_name; without recovery that
    UniqueViolationError propagates out of write_names and aborts the whole
    observation, which the route reports as `db_constraint_violation` —
    discarding links, addresses, role assignments and events over a cosmetic
    display-name repair.
    """
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                _HEAL_PERSON_SELECT_SQL,
                person_id,
                list(NO_AUTO_CANONICAL_NAME_TYPES),
            )
            if row is None or row["displays"] or not row["candidate_id"]:
                return
            await conn.execute(_HEAL_PERSON_UPDATE_SQL, row["candidate_id"])
    except asyncpg.exceptions.UniqueViolationError:
        # Another session claimed the slot between the probe and the update.
        # Expected and benign — their row satisfies the goal just as well.
        logger.debug("heal_person_canonical: lost race for person=%s", person_id)
    except asyncpg.exceptions.PostgresError as exc:
        # Anything else: a deadlock, a serialization failure, or a real defect
        # (UndefinedColumnError, InsufficientPrivilegeError — all PostgresError
        # subclasses). Still swallowed, because escaping here aborts the
        # enclosing observation and discards its links, addresses, role
        # assignments and events over a cosmetic display-name repair (CR4 #41).
        #
        # But WARNING, not debug (CR5 #47): configure_logging defaults to INFO,
        # so a debug line is dropped in production. A typo'd UPDATE or a revoked
        # grant would otherwise stop all healing silently while the merge route
        # still flashes success and the person stays blank.
        logger.warning(
            "heal_person_canonical: heal failed for person=%s (%s: %s)",
            person_id,
            type(exc).__name__,
            exc,
        )


class Disposition(StrEnum):
    AUTO_ATTACHED = "auto-attached"
    NEW = "new"
    # #391: id-addressed void — the entity was archived. Emitted only by the
    # assignment observation surface (op="retract"); the other single-object
    # observation endpoints never return it.
    RETRACTED = "retracted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AssignmentResolution:
    """Outcome of resolving an assignment observation (#311/#391).

    Mirrors the result dataclasses the newer observation surfaces return
    (``EventResult`` / ``CitationResult`` / ``RelationshipResult``) rather than
    the positional tuple :func:`resolve_assignment` returned through #391.

    ``unapplied`` carries the field names supplied but not written, so a producer
    can stop retrying (#311). ``attached_archived`` marks the #391
    anti-resurrection attach — an archived twin matched — and obliges the caller
    to skip every ancillary write: links / contact methods / addresses on a
    retracted row are meaningless, and each one fires a #327 touch trigger,
    emitting an ``entity_changes`` row for an entity subscribers have dropped.
    """

    assignment_id: str
    disposition: Disposition
    reason: str | None = None
    unapplied: list[str] = field(default_factory=list)
    attached_archived: bool = False


class EventDisposition(StrEnum):
    """Per-event outcome of an event observation (#321)."""

    NEW = "new"
    AUTO_ATTACHED = "auto-attached"  # matched (content dedup) or id-addressed no-op
    UPDATED = "updated"  # id-addressed refine-in-place changed mutable fields
    RETRACTED = "retracted"  # #322: id-addressed void — event archived
    REJECTED = "rejected"


class EventRejectReason(StrEnum):
    """Machine-readable event rejection reasons (#321).

    Transient (self-heals on a later cycle): ``linked_entity_unresolved``.
    All others are terminal. #85 rejection-visibility / #112 non-convergence
    bucket by these slugs.
    """

    LINKED_ENTITY_UNRESOLVED = "linked_entity_unresolved"  # transient
    IDENTITY_IMMUTABLE = "identity_immutable"
    EVENT_NOT_FOUND = "event_not_found"
    PROVENANCE_CONFLICT = "provenance_conflict"
    APPLIES_TO_MISMATCH = "applies_to_mismatch"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    UNKNOWN_EVENT_TYPE = "unknown_event_type"
    INVALID = "invalid"


@dataclass(frozen=True)
class EventResult:
    """Outcome of applying one event claim; the writer's per-event return unit."""

    disposition: EventDisposition
    event_id: str | None = None
    reason: str | None = None  # EventRejectReason slug on REJECTED, else None
    # #477: the AUTO_ATTACHED match was an *archived* row — the anti-resurrection
    # content dedup, or a retract re-emitted against an already-archived event.
    # Surfaced on the wire so `auto-attached` stops meaning two different things.
    attached_archived: bool = False


# The mutable field set on an id-addressed (pm_event_id) update. Identity —
# event_type and linked_entity — is immutable (a change there is a *different*
# event → identity_immutable). The date is treated as a unit (all six partial
# columns replaced together) so a year-only sharpening clears finer precision.
_MUTABLE_EVENT_DATE_COLS = (
    "event_year",
    "event_month",
    "event_day",
    "event_hour",
    "event_minute",
    "event_second",
)


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
      - is_canonical hint: honoured only if the person has no canonical name at
        all (the slot is person-wide under #308, not per name_type) or, for an
        org, none for the whole org; never displaces. A hint on a
        NEVER_CANONICAL_NAME_TYPES row is ignored rather than raising.
      - person, no hint: first-wins auto-promotion (#308b) — symmetric with the
        org branch, so a silent client still yields a displayable person.
    """
    if entity_type == "person":
        # Exactly one name per write may claim canonical; hint wins, else priority.
        canonical_target = _person_canonical_target(names)
        guarded_sql = _person_name_insert_sql(f"($9 AND {_CANONICAL_GUARD})")
        unpromoted_sql = _person_name_insert_sql("FALSE")
        # Tracks whether this write already claimed the canonical slot, so the
        # heal pass below can be skipped — it is a guaranteed no-op in that case,
        # and costs a round trip against a remote DB (#308, CR2 #11).
        displays = False
        for idx, n in enumerate(names):
            # Dedup on the full identity, not the bare name (#308, CR3 #22): a
            # `legal` name and an `mrz` rendering can share a string while being
            # different claims, and a name-only key silently discarded the second.
            #
            # The probe is deliberately visibility-blind, but the pick is not
            # (CR6 #53): merge preserves rows identical except visibility (#121
            # inheritance), and an unordered fetchrow matched whichever twin the
            # heap offered first — so a `parts` payload could attach to the
            # hidden copy. Prefer the public twin, tie-break on id.
            existing = await conn.fetchrow(
                "SELECT id FROM person_names"
                " WHERE person_id=$1 AND name=$2 AND name_type=$3"
                "   AND locale IS NOT DISTINCT FROM $4"
                "   AND script IS NOT DISTINCT FROM $5"
                " ORDER BY (visibility = 'public') DESC, id"
                " LIMIT 1",
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
                        claimed = await conn.fetchval(guarded_sql, *base_args, eligible)
                        # chk_person_canonical_is_public makes a canonical row
                        # necessarily visible, so the flag alone answers "does
                        # this person now display?".
                        displays = displays or bool(claimed)
                        if n.parts is not None:
                            await _write_person_name_parts(conn, name_id, n.parts, is_new=True)
                except asyncpg.exceptions.UniqueViolationError:
                    # A concurrent writer claimed the person's canonical slot
                    # between our NOT EXISTS guard and this INSERT. Land the name
                    # unpromoted rather than lose the observation — their row
                    # satisfies the display requirement just as well. Retry runs
                    # outside the failed savepoint; reusing it would hit
                    # "current transaction is aborted".
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
            await heal_person_canonical(conn, entity_id)
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
        # The links touch trigger (#327) emits the parent entity_changes row.
        await conn.execute(
            "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            entity_type,
            entity_id,
            link.url,
            link_type_id,
        )


async def _null_fill_metadata(
    conn,
    table: str,
    col: str,
    pk_val: str,
    value: str,
) -> None:
    """Fill col in the pk_val row of table only where it is currently NULL.

    table/col are caller-controlled string constants, not user input. The parent
    ``entity_changes`` 'updated' signal is emitted by the table's touch-parent
    trigger (``entity_addresses`` / ``contact_methods``, #327), so no manual
    outbox write is needed here.
    """
    await conn.execute(
        f"UPDATE {table} SET {col}=$1 WHERE id=$2 AND {col} IS NULL",
        value,
        pk_val,
    )


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
                # contact_methods has a touch trigger (#327) — the UPDATE
                # self-emits, like entity_addresses; no manual outbox write.
                await _null_fill_metadata(
                    conn,
                    "contact_methods",
                    "display_label",
                    existing["id"],
                    cm.display_label,
                )
            continue
        # The contact_methods touch trigger (#327) emits the parent entity_changes row.
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
      (docs/SCHEMA.md §"Address validity windows (#181)", #256 decision).

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
                    "entity_addresses",
                    "display_name",
                    existing["id"],
                    addr.display_name,
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

    A typed observation that matches an *untyped* non-jurisdictional role fills
    its ``role_type_id`` in place (upgrade-on-match, #266) — only a NULL is filled,
    never a reclassification — so ongoing ingest self-classifies legacy rows.

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
    forbids_qualifier = False
    if role_type is not None:
        rt = await conn.fetchrow(
            "SELECT id, requires_qualifier, forbids_qualifier FROM role_types WHERE slug=$1",
            role_type,
        )
        if rt is None:
            return "", Disposition.REJECTED, f"role_type_not_found: {role_type!r}"
        role_type_id = rt["id"]
        requires_qualifier = rt["requires_qualifier"]
        forbids_qualifier = rt["forbids_qualifier"]

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
        # The mirror (#302): a positionless office must REFUSE a qualifier, not
        # merely tolerate its absence. requires_qualifier=False only permits NULL;
        # without this a stray qualifier mints a second, self-contradictory role
        # for the district. Blank is absence, so it falls through to the normal
        # positionless create rather than rejecting.
        if forbids_qualifier and (qualifier or "").strip():
            return "", Disposition.REJECTED, f"qualifier_forbidden: role_type={role_type!r}"
        # Blank is absence everywhere downstream — match key, stored value, title
        # synthesis. Normalize once here so a direct caller's "" can't persist as
        # a distinct blank-qualifier seat (the case the reject above guards for
        # requires_qualifier offices, and which no flag guards for the rest).
        if not (qualifier or "").strip():
            qualifier = None

    # A qualifier only disambiguates roles with a jurisdiction; drop it for roles
    # without one so it never persists without a jurisdiction. The
    # chk_role_qualifier_needs_jurisdiction CHECK backstops other insert paths.
    if jurisdiction_id is None:
        qualifier = None

    # NOTE: title-mode matching below keys on (org, lower(title)) and ignores
    # role_type_id — by design (#266). role_type_id is persisted on create and
    # aggregates cleanly, but it is *not* a match key — (org, title) stays the
    # sole key, so a producer's emitter needs no change, and the DB uq_role_org_title
    # (unique on (org, title) regardless of role_type) already forbids two
    # same-title non-jurisdictional roles from coexisting. (The original #266 ask —
    # adding role_type to the match key — was withdrawn as moot given that index.)
    # A pre-existing *untyped* role matched by a *typed* observation is upgraded in
    # place below (role_type_id NULL → filled, never reclassified), so ongoing
    # ingest self-classifies legacy free-text rows.

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
            "SELECT id, role_type_id FROM roles"
            " WHERE organization_id=$1 AND lower(title)=lower($2)"
            " AND jurisdiction_id IS NULL AND archived_at IS NULL",
            organization_id,
            title,
        )
    if existing:
        # Upgrade-on-match (#266): a typed observation that lands on an untyped
        # non-jurisdictional role fills role_type_id in place, so ongoing ingest
        # self-classifies legacy free-text rows. Only a NULL is filled — never a
        # reclassification of an already-typed row (which would silently rewrite a
        # human/prior classification). The jurisdictional branch already matches on
        # role_type, so this applies solely to the (org, title) path.
        if (
            jurisdiction_id is None
            and role_type_id is not None
            and existing["role_type_id"] is None
        ):
            await conn.execute(
                "UPDATE roles SET role_type_id=$1 WHERE id=$2", role_type_id, existing["id"]
            )
            logger.info(
                "Upgraded untyped role id=%s to role_type=%s via matched observation",
                existing["id"],
                role_type,
            )
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


async def write_role_assignments(
    conn, person_id: str, source_key_id: str | None, role_assignments: list
) -> None:
    """Append role assignments. No-op if open (no end_date) assignment exists for same role.

    New rows record the observing key as provenance (``source_key_id``, #311).

    Anti-resurrection (#391): this embedded path is the second door onto the same
    ``(person, role, start_date)`` identity as :func:`resolve_assignment`, so it
    honours a retract too. Its own dedup keys on the *open* tenure, which an
    archived row no longer matches — without the explicit archived-twin skip a
    re-emit would mint a fresh active twin and defeat the retract.
    """
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
        archived_twin = await _find_archived_assignment_twin(
            conn, person_id, ra.role_id, start_date
        )
        if archived_twin is not None:
            logger.info(
                "write_role_assignments: skipping retracted twin id=%s (no resurrect)",
                archived_twin["id"],
            )
            continue
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, start_date, end_date, source_key_id)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            generate_id(),
            person_id,
            ra.role_id,
            start_date,
            end_date,
            source_key_id,
        )


async def _set_org_parent(
    conn, organization_id: str, parent_id: str, source_key_id: str | None, where_sql: str
) -> None:
    """Run the parent-setting UPDATE, mapping an org-cycle trigger raise to a rejection.

    Owns the shared ``UPDATE organizations SET parent_id …, source_key_id …``
    statement; the caller supplies only the differing row predicate via
    ``where_sql`` (a trusted static literal — ``"parent_id IS NULL"`` for the
    write-if-null fill, ``"archived_at IS NULL"`` for the authoritative reparent).

    On an ``organizations`` UPDATE the only ``PL/pgSQL RAISE`` (SQLSTATE P0001 →
    ``asyncpg.RaiseError``) is ``trg_no_org_cycle``; the endpoint handler does not
    catch ``RaiseError``, so an unwrapped raise would surface as a 500. Both the
    write-if-null and authoritative paths share this mapping (#334).
    """
    sql = (
        "UPDATE organizations SET parent_id=$1, source_key_id=COALESCE(source_key_id, $3)"
        f" WHERE id=$2 AND {where_sql}"  # noqa: S608 — where_sql is a trusted literal, not input
    )
    try:
        await conn.execute(sql, parent_id, organization_id, source_key_id)
    except asyncpg.RaiseError as exc:
        logger.warning("write_org_parent: cycle org=%s parent=%s", organization_id, parent_id)
        raise ObservationRejected("parent_cycle") from exc


async def write_org_parent(
    conn,
    organization_id: str,
    parent_id: str,
    *,
    source_key_id: str | None = None,
    authoritative: bool = False,
) -> None:
    """Set organizations.parent_id from an observation (#334).

    Two modes, mirroring the assignment split (#311):

    - **Non-authoritative** (``authoritative=False``, the natural /
      external-identifier match): write-if-null. Fills the parent only when it
      is currently NULL and claims ``source_key_id`` on that first write; an org
      that already has a parent is left untouched — a natural-key match never
      reparents. Filling a NULL parent with a descendant would close a loop; that
      is rejected ``parent_cycle`` (mapped from ``trg_no_org_cycle``) rather than
      bubbling a 500.
    - **Authoritative** (``authoritative=True``, the id-addressed ``pm_org_id``
      path): the producer proves it means exactly this org, so the supplied
      parent *replaces* the stored one (reparent). Gated on provenance —
      rejected ``source_key_mismatch`` when the org's ``source_key_id`` is
      non-NULL and differs from the caller's; a NULL source (admin-set or
      pre-#334) is claimed (COALESCE) on write. An identical redelivery (parent
      already as supplied) is a quiet no-op *before* the gate, so a mirroring
      producer never sees a rejection. Also rejects ``parent_not_found``
      (unknown/archived parent) and ``parent_cycle`` — a self-parent (caught by
      the app pre-check) or an ancestor loop at any depth (caught from the
      recursive ``trg_no_org_cycle``).

    Must run inside the caller's transaction so a rejection rolls the whole
    observation back.
    """
    if not authoritative:
        await _set_org_parent(conn, organization_id, parent_id, source_key_id, "parent_id IS NULL")
        return

    row = await conn.fetchrow(
        "SELECT parent_id, source_key_id FROM organizations WHERE id=$1 AND archived_at IS NULL",
        organization_id,
    )
    if row is None:
        raise ObservationRejected("org_not_found")
    if row["parent_id"] == parent_id:
        # Idempotent — parent already as supplied. Checked before the authority
        # gate so an identical redelivery by a foreign key stays a quiet no-op.
        return

    if parent_id == organization_id:
        raise ObservationRejected("parent_cycle")
    parent_exists = await conn.fetchval(
        "SELECT 1 FROM organizations WHERE id=$1 AND archived_at IS NULL", parent_id
    )
    if not parent_exists:
        raise ObservationRejected("parent_not_found")

    if row["source_key_id"] is not None and row["source_key_id"] != source_key_id:
        logger.warning(
            "write_org_parent: source mismatch org=%s owner=%s caller=%s",
            organization_id,
            row["source_key_id"],
            source_key_id,
        )
        raise ObservationRejected("source_key_mismatch")

    await _set_org_parent(conn, organization_id, parent_id, source_key_id, "archived_at IS NULL")
    logger.info("Reparented org id=%s parent_id=%s", organization_id, parent_id)


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


class _EventRejected(Exception):
    """Internal: a per-event domain rejection carrying an EventRejectReason slug."""

    def __init__(self, reason: EventRejectReason) -> None:
        self.reason = reason
        super().__init__(reason)


class _SavepointRollback(Exception):
    """Internal: unwind a per-event savepoint once its result is decided.

    Raised inside ``apply_event_observations``' per-event ``conn.transaction()``
    to roll the savepoint back while carrying the already-computed result out —
    distinct from ``_EventRejected`` (a rejection *signal* from a writer) so the
    two control-flow roles never get conflated.
    """

    def __init__(self, result: "EventResult") -> None:
        self.result = result
        super().__init__()


async def _validate_event_place(conn, place_addr_id: str | None) -> None:
    """Reject an event whose ``event_place_address_id`` is missing or too coarse."""
    if not place_addr_id:
        return
    addr_row = await conn.fetchrow("SELECT id, precision FROM addresses WHERE id=$1", place_addr_id)
    if addr_row is None:
        raise _EventRejected(EventRejectReason.INVALID)
    # NULL precision = pre-geocoding / historical record — allowed intentionally.
    if addr_row["precision"] is not None and addr_row["precision"] not in EVENT_PLACE_PRECISIONS:
        raise _EventRejected(EventRejectReason.INVALID)


async def _linked_entity_exists(conn, linked_type: str, linked_id: str) -> bool:
    """True if a linked entity id resolves to a live PM entity of the given kind."""
    table = "organizations" if linked_type == "organization" else "people"
    return bool(await conn.fetchval(f"SELECT 1 FROM {table} WHERE id=$1", linked_id))


async def _apply_one_event(
    conn: asyncpg.Connection,
    entity_id: str,
    entity_type: str,
    key_id: str | None,
    ev,
) -> EventResult:
    """Apply one event claim; return its per-event outcome (never raises for a
    domain rejection — those come back as ``EventResult(REJECTED, reason=...)``).

    Two modes (#321):

    - ``pm_event_id`` present → **refine-in-place**. Identity (event_type,
      linked_entity) is immutable; only the mutable set (date/notes/place/
      visibility) updates. Gated on ``source_key_id`` provenance (same-or-NULL);
      an unchanged re-emit is a diff-before-write no-op (``auto-attached``, no
      clock bump).
    - absent → **natural create** with content dedup (``auto-attached`` on match,
      else ``new``). A required linked entity that is not yet anchored yields the
      transient ``linked_entity_unresolved``.
    """
    # Resolve event type (slug XOR id, enforced by the schema validator).
    if ev.event_type_id is None:
        etype = await conn.fetchrow(
            "SELECT id, applies_to, requires_year, requires_linked_entity"
            " FROM entity_event_types WHERE slug=$1",
            ev.event_type_slug,
        )
    else:
        etype = await conn.fetchrow(
            "SELECT id, applies_to, requires_year, requires_linked_entity"
            " FROM entity_event_types WHERE id=$1",
            ev.event_type_id,
        )
    if etype is None:
        return EventResult(EventDisposition.REJECTED, None, EventRejectReason.UNKNOWN_EVENT_TYPE)
    event_type_id = etype["id"]

    if etype["applies_to"] != "both" and etype["applies_to"] != entity_type:
        return EventResult(EventDisposition.REJECTED, None, EventRejectReason.APPLIES_TO_MISMATCH)

    try:
        if ev.op == "retract":
            return await _retract_event(conn, event_type_id, key_id, ev)
        if ev.pm_event_id is not None:
            return await _refine_event_in_place(
                conn, event_type_id, etype["requires_year"], key_id, ev
            )
        return await _create_event(conn, entity_id, entity_type, event_type_id, key_id, etype, ev)
    except _EventRejected as exc:
        return EventResult(EventDisposition.REJECTED, None, exc.reason)


async def _refine_event_in_place(
    conn, event_type_id: str, requires_year: bool, key_id: str | None, ev
) -> EventResult:
    """id-addressed update: immutable identity, diff-before-write, provenance gate."""
    existing = await conn.fetchrow(
        """SELECT id, event_type_id, linked_entity_type, linked_entity_id, source_key_id,
                  event_year, event_month, event_day, event_hour, event_minute, event_second,
                  event_place_text, event_place_address_id, notes, visibility
           FROM entity_events WHERE id=$1 AND archived_at IS NULL""",
        ev.pm_event_id,
    )
    if existing is None:
        raise _EventRejected(EventRejectReason.EVENT_NOT_FOUND)

    # Identity is immutable — a change to event_type or a supplied, differing
    # linked_entity is a *different* event, never a silent reclassify.
    if event_type_id != existing["event_type_id"]:
        raise _EventRejected(EventRejectReason.IDENTITY_IMMUTABLE)
    if ev.linked_entity_id is not None and (
        ev.linked_entity_id != existing["linked_entity_id"]
        or ev.linked_entity_type != existing["linked_entity_type"]
    ):
        raise _EventRejected(EventRejectReason.IDENTITY_IMMUTABLE)

    # Desired mutable state (last-writer-wins; date is a unit).
    new_vals = {
        "event_year": ev.event_year,
        "event_month": ev.event_month,
        "event_day": ev.event_day,
        "event_hour": ev.event_hour,
        "event_minute": ev.event_minute,
        "event_second": ev.event_second,
        "event_place_text": ev.event_place_text,
        "event_place_address_id": ev.event_place_address_id,
        "notes": ev.notes,
        "visibility": ev.visibility,
    }
    # Diff-before-write: an unchanged re-emit must not UPDATE (would bump
    # updated_at and re-arm the producer↔PM ping-pong). Checked first — before
    # the provenance gate (so an identical redelivery by a foreign key stays a
    # no-op) and before any validation (the stored row is already valid).
    if all(existing[col] == val for col, val in new_vals.items()):
        return EventResult(EventDisposition.AUTO_ATTACHED, existing["id"])

    if existing["source_key_id"] is not None and existing["source_key_id"] != key_id:
        logger.warning(
            "event refine source mismatch event=%s owner=%s caller=%s",
            existing["id"],
            existing["source_key_id"],
            key_id,
        )
        raise _EventRejected(EventRejectReason.PROVENANCE_CONFLICT)

    # Merged-state validity: a refine must not clear a required year (which would
    # strand a founded/dissolved event with no lifespan bound). Mirrors _create_event.
    if requires_year and ev.event_year is None:
        raise _EventRejected(EventRejectReason.MISSING_REQUIRED_FIELD)

    await _validate_event_place(conn, ev.event_place_address_id)

    await conn.execute(
        """UPDATE entity_events SET
               event_year=$2, event_month=$3, event_day=$4,
               event_hour=$5, event_minute=$6, event_second=$7,
               event_place_text=$8, event_place_address_id=$9, notes=$10, visibility=$11,
               source_key_id=COALESCE(source_key_id, $12)
           WHERE id=$1 AND archived_at IS NULL""",
        existing["id"],
        ev.event_year,
        ev.event_month,
        ev.event_day,
        ev.event_hour,
        ev.event_minute,
        ev.event_second,
        ev.event_place_text,
        ev.event_place_address_id,
        ev.notes,
        ev.visibility,
        key_id,
    )
    logger.info("Refined entity_event id=%s", existing["id"])
    return EventResult(EventDisposition.UPDATED, existing["id"])


async def _retract_event(conn, event_type_id: str, key_id: str | None, ev) -> EventResult:
    """id-addressed void (#322): archive the event so the outbox drops the anchor.

    The only correction for a dateless linked event (``succeeded_by`` /
    ``split_from`` / ``merged_with``), which has no mutable field to refine — a
    re-link is create-new + retract-old. The lookup is **not** archived-filtered
    (unlike the refine path): an already-archived event must no-op, so a producer
    that re-emits the retract every cycle doesn't re-bump the org LWW clock.

    - no ``pm_event_id`` → ``invalid`` (retract is always id-addressed)
    - id unresolved → ``event_not_found``
    - already archived → diff-gate no-op (``auto-attached``, no clock bump);
      checked before provenance so a foreign re-emit stays quiet (mirrors refine)
    - ``event_type`` differs from the stored row → ``identity_immutable``
    - live event, foreign non-NULL ``source_key_id`` → ``provenance_conflict``
    - else archive it → ``retracted`` (the UPDATE fires the org-touch trigger →
      outbox row → subscriber drops the stale anchor)
    """
    if ev.pm_event_id is None:
        raise _EventRejected(EventRejectReason.INVALID)

    existing = await conn.fetchrow(
        "SELECT id, event_type_id, linked_entity_type, linked_entity_id,"
        " source_key_id, archived_at FROM entity_events WHERE id=$1",
        ev.pm_event_id,
    )
    if existing is None:
        raise _EventRejected(EventRejectReason.EVENT_NOT_FOUND)

    # Diff-before-write: an already-archived event is a no-op — no UPDATE, no
    # clock bump (a stateful producer re-emits the retract every cycle). Checked
    # before the provenance gate so a foreign redelivery stays quiet, exactly as
    # the refine no-op does.
    if existing["archived_at"] is not None:
        return EventResult(EventDisposition.AUTO_ATTACHED, existing["id"], attached_archived=True)

    # Identity is immutable — a retract that names a different event_type, or a
    # supplied linked_entity that doesn't match, is addressing the wrong event
    # (likely a copy-paste pm_event_id), never a silent void. Symmetric with the
    # refine identity guard (#322 CR3).
    if event_type_id != existing["event_type_id"]:
        raise _EventRejected(EventRejectReason.IDENTITY_IMMUTABLE)
    if ev.linked_entity_id is not None and (
        ev.linked_entity_id != existing["linked_entity_id"]
        or ev.linked_entity_type != existing["linked_entity_type"]
    ):
        raise _EventRejected(EventRejectReason.IDENTITY_IMMUTABLE)

    if existing["source_key_id"] is not None and existing["source_key_id"] != key_id:
        logger.warning(
            "event retract source mismatch event=%s owner=%s caller=%s",
            existing["id"],
            existing["source_key_id"],
            key_id,
        )
        raise _EventRejected(EventRejectReason.PROVENANCE_CONFLICT)

    await conn.execute(
        """UPDATE entity_events
               SET archived_at = now(), source_key_id = COALESCE(source_key_id, $2)
           WHERE id=$1 AND archived_at IS NULL""",
        existing["id"],
        key_id,
    )
    logger.info("Retracted entity_event id=%s", existing["id"])
    return EventResult(EventDisposition.RETRACTED, existing["id"])


async def _create_event(
    conn, entity_id: str, entity_type: str, event_type_id: str, key_id: str | None, etype, ev
) -> EventResult:
    """Natural create with content dedup; validates required + linked-entity fields."""
    if etype["requires_year"] and ev.event_year is None:
        raise _EventRejected(EventRejectReason.MISSING_REQUIRED_FIELD)
    if etype["requires_linked_entity"] and not ev.linked_entity_id:
        raise _EventRejected(EventRejectReason.MISSING_REQUIRED_FIELD)

    # A supplied linked entity must resolve; an as-yet-unanchored one is the
    # transient case (ordering-tolerance — heals on a later producer cycle).
    if ev.linked_entity_id is not None and not await _linked_entity_exists(
        conn, ev.linked_entity_type, ev.linked_entity_id
    ):
        raise _EventRejected(EventRejectReason.LINKED_ENTITY_UNRESOLVED)

    await _validate_event_place(conn, ev.event_place_address_id)

    # Dedup: same event type + same partial date + same linked_entity_id = skip.
    # Matches archived rows too (no archived_at filter) — a retract is
    # authoritative, so a re-observation of retracted content auto-attaches to the
    # archived row rather than resurrecting it. Mirrors the address dateless-
    # reobservation anti-resurrection rule (#322 CR round 2; write_addresses).
    existing = await conn.fetchrow(
        """SELECT id, archived_at FROM entity_events
           WHERE entity_id = $1 AND entity_type = $2 AND event_type_id = $3
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
        # #477: the dedup is deliberately not archived-filtered, so the match may
        # be a retracted row. Label which one it was — a producer re-emitting
        # content PM has voided otherwise reads an ordinary `auto-attached`.
        return EventResult(
            EventDisposition.AUTO_ATTACHED,
            existing["id"],
            attached_archived=existing["archived_at"] is not None,
        )

    new_id = generate_id()
    await conn.execute(
        """INSERT INTO entity_events
           (id, entity_type, entity_id, event_type_id,
            event_year, event_month, event_day, event_hour, event_minute, event_second,
            event_place_text, event_place_address_id,
            linked_entity_type, linked_entity_id,
            notes, visibility, source_key_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)""",
        new_id,
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
        ev.event_place_address_id,
        ev.linked_entity_type,
        ev.linked_entity_id,
        ev.notes,
        ev.visibility,
        key_id,
    )
    return EventResult(EventDisposition.NEW, new_id)


async def write_entity_events(
    conn: asyncpg.Connection,
    entity_id: str,
    entity_type: str,
    key_id: str | None,
    events: list,
) -> list[EventResult]:
    """Apply embedded event claims **all-or-nothing** (#321).

    Each event resolves to ``new`` / ``auto-attached`` / ``updated``; the first
    rejection raises ``ObservationRejected`` carrying the reason slug, rolling the
    whole observation back (the pre-#321 contract, now slug-typed). Returns the
    per-event results on full success. For per-event partial-success semantics use
    ``apply_event_observations``.
    """
    results: list[EventResult] = []
    for ev in events:
        result = await _apply_one_event(conn, entity_id, entity_type, key_id, ev)
        if result.disposition is EventDisposition.REJECTED:
            raise ObservationRejected(result.reason)
        results.append(result)
    return results


async def apply_event_observations(
    conn: asyncpg.Connection,
    entity_id: str,
    entity_type: str,
    key_id: str | None,
    events: list,
) -> list[EventResult]:
    """Apply event claims **partial-success** (#321) — the event-native surface.

    Each event runs in its own savepoint: a rejection — a domain reason slug or
    a DB constraint violation — rolls back only that event's writes and is
    reported alongside the ones that landed. This is what gives ordering-tolerance
    for free — a ``succeeded_by`` ahead of its successor comes back
    ``linked_entity_unresolved`` while its siblings commit, and heals next cycle.
    **Must run inside the caller's transaction** so the whole batch shares one
    outer commit.
    """
    results: list[EventResult] = []
    for ev in events:
        try:
            async with conn.transaction():
                result = await _apply_one_event(conn, entity_id, entity_type, key_id, ev)
                if result.disposition is EventDisposition.REJECTED:
                    raise _SavepointRollback(result)  # unwind this event's savepoint
        except _SavepointRollback as sr:
            result = sr.result
        except (
            asyncpg.CheckViolationError,
            asyncpg.ForeignKeyViolationError,
            asyncpg.UniqueViolationError,
        ):
            # A per-event DB constraint (e.g. a partial-date-chain check) — the
            # savepoint already rolled back; isolate it so siblings still land
            # rather than 500-ing the whole batch (CR #1).
            result = EventResult(EventDisposition.REJECTED, None, EventRejectReason.INVALID)
        results.append(result)
    return results


async def _find_archived_assignment_twin(
    conn, person_id: str, role_id: str, start_date: date | None
) -> asyncpg.Record | None:
    """Return the most recently archived assignment on this identity, if any (#391).

    Shared by both create doors — :func:`resolve_assignment` and
    :func:`write_role_assignments` — because an anti-resurrection rule that lives
    in two hand-copied queries drifts: the two copies were already inconsistent
    (one lacked the ``ORDER BY``) within the commit that introduced them.

    ``uq_role_assignment_person_role_start`` is partial on active rows, so
    several archived rows may share one identity; the newest wins. Callers that
    only need existence ignore the extra columns.
    """
    return await conn.fetchrow(
        "SELECT id, end_date, is_current FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND start_date IS NOT DISTINCT FROM $3"
        "   AND archived_at IS NOT NULL"
        " ORDER BY archived_at DESC, id DESC LIMIT 1",
        person_id,
        role_id,
        start_date,
    )


async def resolve_assignment(
    conn,
    person_id: str,
    role_id: str,
    start_date: date | None,
    *,
    end_date: date | None = None,
    is_current: bool | None = None,
    notes: str | None = None,
    source_key_id: str | None = None,
) -> AssignmentResolution:
    """Match or create a role assignment by (person_id, role_id, start_date).

    Returns an :class:`AssignmentResolution`. ``disposition`` is AUTO_ATTACHED if
    an active (non-archived) match is found, NEW if created, REJECTED if
    person_id or role_id does not exist; ``reason`` is a human-readable string on
    REJECTED, None otherwise.

    On AUTO_ATTACHED (#311):

    - a dated ``end_date`` **closes an open tenure in place** (stored end NULL →
      supplied value, ``is_current`` → FALSE — a dated end implies ended), the
      one monotonic enrichment the natural-key path applies. Gated on
      provenance: only when the row's ``source_key_id`` is NULL (claimed on
      apply) or matches the caller's.
    - any other delta — a differing non-NULL ``end_date``, an ``is_current``
      flip — is never applied; the field name is returned in ``unapplied`` so
      the producer can stop retrying and escalate to a pm-native id-addressed
      update (``update_assignment_fields``).

    ``is_current=None`` means omitted (tri-state, #311); NEW inserts treat it
    as FALSE. ``notes`` is create-only and never reported unapplied.
    """
    person_exists = await conn.fetchval(
        "SELECT 1 FROM people WHERE id=$1 AND archived_at IS NULL", person_id
    )
    if not person_exists:
        logger.warning("resolve_assignment: unknown person_id=%r", person_id)
        return AssignmentResolution("", Disposition.REJECTED, f"person_not_found: {person_id!r}")

    role_exists = await conn.fetchval(
        "SELECT 1 FROM roles WHERE id=$1 AND archived_at IS NULL", role_id
    )
    if not role_exists:
        logger.warning("resolve_assignment: unknown role_id=%r", role_id)
        return AssignmentResolution("", Disposition.REJECTED, f"role_not_found: {role_id!r}")

    existing = await conn.fetchrow(
        "SELECT id, end_date, is_current, source_key_id FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND start_date IS NOT DISTINCT FROM $3"
        "   AND archived_at IS NULL",
        person_id,
        role_id,
        start_date,
    )
    if existing:
        stored_end = existing["end_date"]
        stored_current = existing["is_current"]
        authorized = existing["source_key_id"] is None or existing["source_key_id"] == source_key_id
        if end_date is not None and stored_end is None and is_current is not True and authorized:
            # Close an open tenure in place (#311) — the routine election-cycle
            # update. The request validator already blocks is_current=True with
            # a dated end; the is_current guard holds the same invariant for
            # core callers that bypass the request model.
            await conn.execute(
                "UPDATE role_assignments SET end_date=$2, is_current=FALSE,"
                " source_key_id=COALESCE(source_key_id, $3)"
                " WHERE id=$1",
                existing["id"],
                end_date,
                source_key_id,
            )
            logger.info(
                "Closed role_assignment id=%s end_date=%s on auto-attach",
                existing["id"],
                end_date,
            )
            stored_end, stored_current = end_date, False
        unapplied = []
        if end_date is not None and stored_end != end_date:
            unapplied.append("end_date")
        if is_current is not None and is_current != stored_current:
            unapplied.append("is_current")
        return AssignmentResolution(existing["id"], Disposition.AUTO_ATTACHED, unapplied=unapplied)

    # No active twin. An archived twin means this tenure was retracted (#391) or
    # suppressed by a curator — attach to it rather than minting a fresh active
    # row (anti-resurrection, mirrors events #322 / citations #319 /
    # relationships #301). uq_role_assignment_person_role_start is partial on
    # active rows, so the DB *permits* the re-create; the app declines. Without
    # this a re-emitting producer defeats the retract every sync cycle.
    # Un-retract stays a deliberate admin unarchive.
    archived_twin = await _find_archived_assignment_twin(conn, person_id, role_id, start_date)
    if archived_twin is not None:
        # Every supplied mutable field is withheld and reported — *unconditionally*,
        # unlike the active-row path above, which skips a value equal to what's
        # stored. That equivalence does not transfer: on an active row "equals
        # stored" means the claim is already true in PM, but on a retracted row PM
        # asserts the tenure never existed, so the identical claim is contradicted
        # rather than satisfied. Comparing here left the likeliest payload silent —
        # a producer re-emitting a currently-held tenure sends is_current=true,
        # exactly what the row stored when it was retracted. `notes` stays exempt
        # (create-only, never reported — the #311 rule). The caller adds the
        # ancillary it skips (links/contacts/addresses) to this list.
        unapplied = [
            name
            for name, supplied in (("end_date", end_date), ("is_current", is_current))
            if supplied is not None
        ]
        logger.info(
            "resolve_assignment: attaching to archived twin id=%s (no resurrect)",
            archived_twin["id"],
        )
        return AssignmentResolution(
            archived_twin["id"],
            Disposition.AUTO_ATTACHED,
            unapplied=unapplied,
            attached_archived=True,
        )

    assignment_id = generate_id()
    try:
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, start_date, end_date, is_current, notes, source_key_id)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            assignment_id,
            person_id,
            role_id,
            start_date,
            end_date,
            bool(is_current),
            notes,
            source_key_id,
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
        return AssignmentResolution("", Disposition.REJECTED, "unique_violation")

    logger.info(
        "Created role_assignment id=%s person=%s role=%s start=%s",
        assignment_id,
        person_id,
        role_id,
        start_date,
    )
    return AssignmentResolution(assignment_id, Disposition.NEW)


async def update_assignment_fields(
    conn,
    assignment_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    end_date_set: bool = False,
    is_current: bool | None = None,
    source_key_id: str | None = None,
) -> bool:
    """Authoritatively update an id-addressed tenure's bounds in place (#311).

    The pm-native (``pm_assignment_id``) observation path: the producer proves it
    means exactly this row, so — unlike the natural-key match — supplied fields
    *replace* stored values (supersedes the #289 NULL→dated-only backfill):

    - ``start_date``: a non-None value moves the bound (a start cannot be
      cleared back to NULL); None means omitted.
    - ``end_date``: applied only when ``end_date_set`` (JSON null ≠ omitted —
      the caller passes ``"end_date" in model_fields_set``); an explicit null
      **clears** the bound (reopen).
    - ``is_current``: tri-state; None means omitted. A dated resulting end with
      ``is_current`` omitted implies FALSE (a dated end means ended).

    Provenance (#311): rejected ``source_key_mismatch`` when the row's
    ``source_key_id`` is non-NULL and differs from the caller's; a NULL source
    is claimed (COALESCE) on the first update. An identical redelivery leaves
    the *bounds* alone regardless of source (that check precedes the gate).

    Returns **True when this call claimed provenance** — i.e. the row's
    ``source_key_id`` was NULL and the caller supplied one. #478: an identical
    redelivery against an **unowned** row now claims it with a provenance-only
    UPDATE instead of returning empty-handed. Before that, ``source_key_id``
    could only ever be stamped as a side effect of a value change, so a row
    whose stored span was already exactly right was unclaimable without
    falsifying a date — 6,698 of 11,086 active assignments predate #311 and are
    in precisely that state. An **owned** row (same-source or foreign) still
    takes no write at all from an identical assertion: CR round 1 of #311 ruled
    that a foreign key must not be able to change anything by agreeing, and
    claiming is a change. The provenance-only UPDATE fires the #327 touch
    triggers like any other write, so the claim reaches ``entity_changes``.

    **Must be called inside the caller's transaction** so a rejection rolls the
    whole observation back. Raises ``ObservationRejected`` when the row is
    archived/gone (``assignment_not_found`` — including a row archived between
    this function's read and its write); another key owns the row
    (``source_key_mismatch``, from the gate on the stored value *or* from losing
    a race to a key that claimed it after that read); the merged state would be
    ``is_current`` with a dated end (``is_current_end_date_conflict`` — send an
    explicit ``end_date: null`` to reopen); the merged bounds invert
    (``start_after_end_date``); or the new ``start_date`` collides with a
    sibling tenure sharing (person, role, start_date) (``start_date_conflict``).
    """
    if start_date is None and not end_date_set and is_current is None:
        return False

    row = await conn.fetchrow(
        "SELECT start_date, end_date, is_current, source_key_id FROM role_assignments"
        " WHERE id=$1 AND archived_at IS NULL",
        assignment_id,
    )
    if row is None:
        raise ObservationRejected("assignment_not_found")

    # A NULL source is unowned: the first caller to assert this row's bounds
    # claims it. Computed once — both the identical-assertion branch below and
    # the ordinary UPDATE report the same fact.
    claims_provenance = row["source_key_id"] is None and source_key_id is not None

    new_start = start_date if start_date is not None else row["start_date"]
    new_end = end_date if end_date_set else row["end_date"]
    new_current = is_current if is_current is not None else row["is_current"]
    if is_current is None and new_end is not None:
        new_current = False  # a dated end implies the tenure has ended

    if (new_start, new_end, new_current) == (
        row["start_date"],
        row["end_date"],
        row["is_current"],
    ):
        # Idempotent — fields already as supplied. Checked before the authority
        # gate so an identical redelivery by a foreign key stays a quiet no-op
        # (CR round 1, #311); the row's stored state is valid, so the merged
        # values need no constraint checks either.
        if not claims_provenance:
            return False
        # #478: the row is *unowned*, so agreeing with it is how a producer
        # claims provenance on a pre-#311 minting. Provenance only — the bounds
        # are already right, and rewriting them would risk
        # `uq_role_assignment_person_role_start` for no gain. The re-checked
        # `source_key_id IS NULL` makes the claim lose a concurrent race rather
        # than overwrite a claim committed since the SELECT, and RETURNING makes
        # the answer come from the write rather than the stale read — a producer
        # told `provenance_claimed: true` must actually own the row. CR round 1
        # is preserved: an *owned* row took the early return above, so a foreign
        # key still cannot alter it by agreeing.
        claimed_by = await conn.fetchval(
            "UPDATE role_assignments SET source_key_id=$2"
            " WHERE id=$1 AND archived_at IS NULL AND source_key_id IS NULL"
            " RETURNING source_key_id",
            assignment_id,
            source_key_id,
        )
        if claimed_by != source_key_id:
            # Zero rows, two causes that are not the same event (#480 CR3).
            # Another key claiming first is not an error: the producer's
            # assertion still holds — the bounds it sent are what the row
            # stores — and only the claim failed, so stay quiet. The row being
            # archived underneath us is the event the bounds path rejects, and
            # the producer is addressing a row that no longer exists.
            current = await conn.fetchrow(
                "SELECT archived_at FROM role_assignments WHERE id=$1", assignment_id
            )
            if current is None or current["archived_at"] is not None:
                logger.warning(
                    "update_assignment_fields: assignment=%s archived between read and claim",
                    assignment_id,
                )
                raise ObservationRejected("assignment_not_found")
            return False
        logger.info(
            "Claimed provenance on role_assignment id=%s source_key_id=%s",
            assignment_id,
            source_key_id,
        )
        return True

    if row["source_key_id"] is not None and row["source_key_id"] != source_key_id:
        logger.warning(
            "update_assignment_fields: source mismatch assignment=%s owner=%s caller=%s",
            assignment_id,
            row["source_key_id"],
            source_key_id,
        )
        raise ObservationRejected("source_key_mismatch")

    if new_current and new_end is not None:
        raise ObservationRejected("is_current_end_date_conflict")
    if new_start is not None and new_end is not None and new_start > new_end:
        raise ObservationRejected("start_after_end_date")

    try:
        # RETURNING for the same reason as the claim branch above: the reported
        # claim must come from what the write settled on, not the stale read.
        #
        # The ownership predicate repeats the gate 20 lines up *in the write*
        # (#478 CR): that gate tests a value read before this statement, so a key
        # that claimed the row in between would otherwise have another producer's
        # bounds written over its own — the read decides which error to name, the
        # predicate is what actually enforces authority. The claim branch above
        # already worked this way; this is the same technique on the bounds path.
        updated = await conn.fetchrow(
            "UPDATE role_assignments SET start_date=$2, end_date=$3, is_current=$4,"
            " source_key_id=COALESCE(source_key_id, $5)"
            " WHERE id=$1 AND archived_at IS NULL"
            "   AND (source_key_id IS NULL OR source_key_id=$5)"
            " RETURNING id, source_key_id",
            assignment_id,
            new_start,
            new_end,
            new_current,
            source_key_id,
        )
    except asyncpg.UniqueViolationError as exc:
        logger.warning(
            "update start_date collides with sibling tenure assignment=%s start=%s",
            assignment_id,
            new_start,
        )
        raise ObservationRejected("start_date_conflict") from exc

    if updated is None:
        # Zero rows matched, so nothing was written — never log or return success
        # for a write that did not land (#478 CR). The pre-UPDATE gate passed, so
        # the row moved underneath us: archived, or claimed by another key since
        # the SELECT. One diagnostic read to name which; it only runs on a path
        # that should effectively never fire.
        current = await conn.fetchrow(
            "SELECT archived_at, source_key_id FROM role_assignments WHERE id=$1",
            assignment_id,
        )
        if (
            current is not None
            and current["archived_at"] is None
            and current["source_key_id"] not in (None, source_key_id)
        ):
            logger.warning(
                "update_assignment_fields: assignment=%s claimed by %s between read and write",
                assignment_id,
                current["source_key_id"],
            )
            raise ObservationRejected("source_key_mismatch")
        logger.warning(
            "update_assignment_fields: assignment=%s vanished between read and write",
            assignment_id,
        )
        raise ObservationRejected("assignment_not_found")

    logger.info(
        "Updated role_assignment id=%s start_date=%s end_date=%s is_current=%s",
        assignment_id,
        new_start,
        new_end,
        new_current,
    )
    # The COALESCE above already claimed an unowned row; #478 only makes the
    # caller able to say so.
    return claims_provenance and updated["source_key_id"] == source_key_id


async def retract_assignment(
    conn,
    assignment_id: str,
    *,
    person_id: str | None = None,
    role_id: str | None = None,
    source_key_id: str | None = None,
) -> Disposition:
    """id-addressed retraction (#391): archive a tenure the producer disowns.

    The correction for a **data artifact** — a tenure that never happened — which
    neither existing lever expresses: ``end_date`` + ``is_current=False``
    *closes* the tenure (asserts it ended), and simply ceasing to produce it
    orphans the anchored row. Retraction asserts it **never existed**.

    Deliberately **not** routed through :func:`resolve_entity`: that helper's
    pm-native lookup filters ``archived_at IS NULL``, which would turn a re-emit
    of an already-retracted assignment into ``pm_id_not_found`` instead of the
    quiet no-op a stateful producer needs (mirrors ``_retract_event``, #322).

    - already archived → diff-gate no-op (``AUTO_ATTACHED``, no UPDATE, no clock
      bump); checked **before** provenance so a foreign re-emit stays quiet.
      This ordering also places it before the identity guard, so a copy-pasted
      id that lands on an *already-archived* row returns quietly and the caller
      never learns its intended assignment is still live. Deliberate, and the
      same trade #322 made: inverting the order would make every re-emit noisy,
      which is the failure mode that actually recurs every sync cycle.
    - id unresolved → ``assignment_not_found``
    - supplied ``person_id`` / ``role_id`` differing from the stored row →
      ``identity_immutable`` (guards a copy-paste ``pm_assignment_id``)
    - live row, foreign non-NULL ``source_key_id`` → ``source_key_mismatch``
    - else archive → ``RETRACTED``. The UPDATE fires
      ``trg_entity_changes_role_assignments`` (outbox row → subscribers mirror
      ``archived_at`` and drop the anchor) and
      ``trg_cascade_assignment_relationships`` (dependent ``staff_of`` edges
      archive with it, #301).

    A retract is **authoritative**: :func:`resolve_assignment` will not resurrect
    the archived row on re-observation. Un-retract is a deliberate admin
    unarchive only. **Must be called inside the caller's transaction.**
    """
    row = await conn.fetchrow(
        "SELECT person_id, role_id, source_key_id, archived_at FROM role_assignments WHERE id=$1",
        assignment_id,
    )
    if row is None:
        logger.warning("retract_assignment: unknown assignment=%r", assignment_id)
        raise ObservationRejected("assignment_not_found")

    if row["archived_at"] is not None:
        # Idempotent — a producer re-emits the retract every cycle. No UPDATE,
        # no outbox row. Checked before the provenance gate so a foreign
        # redelivery stays quiet, exactly as update_assignment_fields' no-op does.
        return Disposition.AUTO_ATTACHED

    if (person_id is not None and person_id != row["person_id"]) or (
        role_id is not None and role_id != row["role_id"]
    ):
        logger.warning(
            "retract_assignment: identity mismatch assignment=%s stored=(%s,%s) supplied=(%s,%s)",
            assignment_id,
            row["person_id"],
            row["role_id"],
            person_id,
            role_id,
        )
        raise ObservationRejected("identity_immutable")

    if row["source_key_id"] is not None and row["source_key_id"] != source_key_id:
        logger.warning(
            "retract_assignment: source mismatch assignment=%s owner=%s caller=%s",
            assignment_id,
            row["source_key_id"],
            source_key_id,
        )
        raise ObservationRejected("source_key_mismatch")

    await conn.execute(
        "UPDATE role_assignments SET archived_at=NOW(),"
        " source_key_id=COALESCE(source_key_id, $2)"
        " WHERE id=$1 AND archived_at IS NULL",
        assignment_id,
        source_key_id,
    )
    logger.info("Retracted role_assignment id=%s", assignment_id)
    return Disposition.RETRACTED
