"""Role-assignment relationship write logic (#301).

A directional, temporal edge between two role_assignments: the staffer's
assignment (from) serves a principal legislator's seat assignment (to). Models a
person->person staff relationship the flat role model cannot hold, while keeping
the object assignment context (org, role, window) on both sides.

**Identity** = ``(from_assignment_id, to_assignment_id, rel_type_id)`` over active
rows (``uq_assignment_relationship_identity``). ``valid_from`` / ``valid_until`` /
``notes`` are mutable payload, never identity.

Mirrors the events / citations observation model (#321/#322/#319):

- **observe** (default) — no ``pm_relationship_id`` → resolve both endpoints +
  rel_type slug → match identity → refine that active row in place
  (diff-before-write no-op → ``auto-attached``) else anti-resurrect an archived
  twin (``auto-attached``) else create (``new``); ``pm_relationship_id`` →
  id-addressed refine (identity immutable).
- **retract** — ``op="retract"`` archives the id-addressed row (``archived_at``,
  never hard-delete); already-archived is a no-op.

Provenance: ``source_key_id`` same-or-NULL gate on refine / retract.

Temporal integrity is **not** enforced on the observation path — it records
freely and the daily audit reconciles (mirrors #307). The admin / direct-write
path calls :func:`check_edge_within_assignments` to reject out-of-window edges.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import asyncpg

from src.core.db import generate_id
from src.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dispositions / reasons
# ---------------------------------------------------------------------------


class RelationshipDisposition(StrEnum):
    """Per-claim outcome of a relationship observation (#301)."""

    NEW = "new"
    AUTO_ATTACHED = "auto-attached"  # identity no-op / anti-resurrection / already-retracted
    UPDATED = "updated"  # refine-in-place changed mutable payload
    RETRACTED = "retracted"  # op=retract archived the row
    REJECTED = "rejected"


class RelationshipRejectReason(StrEnum):
    """Machine-readable relationship rejection reasons (#301).

    Transient (self-heals on a later cycle, ordering-tolerance):
    ``assignment_unresolved``. All others terminal.
    """

    ASSIGNMENT_UNRESOLVED = "assignment_unresolved"  # transient
    REL_TYPE_UNKNOWN = "rel_type_unknown"
    RELATIONSHIP_NOT_FOUND = "relationship_not_found"
    IDENTITY_IMMUTABLE = "identity_immutable"
    PROVENANCE_CONFLICT = "provenance_conflict"
    SELF_RELATIONSHIP = "self_relationship"
    INVALID = "invalid"


@dataclass(frozen=True)
class RelationshipResult:
    """Outcome of applying one relationship claim; the writer's per-claim unit."""

    disposition: RelationshipDisposition
    relationship_id: str | None = None
    reason: str | None = None  # RelationshipRejectReason slug on REJECTED, else None


@dataclass
class RelationshipClaim:
    """One relationship claim (transport-agnostic input to the writer).

    ``op`` is ``observe`` (default) or ``retract``. Natural-key observe needs
    both endpoint ids + ``rel_type``; an id-addressed refine / retract needs
    ``pm_relationship_id`` (endpoint / rel_type, if supplied, must match — identity
    is immutable).
    """

    from_pm_assignment_id: str | None = None
    to_pm_assignment_id: str | None = None
    rel_type: str = "staff_of"
    valid_from: date | None = None
    valid_until: date | None = None
    notes: str | None = None
    op: str = "observe"
    pm_relationship_id: str | None = None


class EdgeOutsideAssignmentWindow(Exception):
    """Admin-path guard: an edge window falls outside an endpoint assignment window."""


class _RelationshipRejected(Exception):
    """Internal: a per-claim domain rejection carrying a reason slug."""

    def __init__(self, reason: RelationshipRejectReason) -> None:
        self.reason = reason
        super().__init__(reason)


class _SavepointRollback(Exception):
    """Internal: unwind a per-claim savepoint once its result is decided."""

    def __init__(self, result: RelationshipResult) -> None:
        self.result = result
        super().__init__()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _live_assignment_exists(conn, assignment_id: str | None) -> bool:
    """True if the id resolves to a live (non-archived) role_assignment."""
    if assignment_id is None:
        return False
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM role_assignments WHERE id=$1 AND archived_at IS NULL",
            assignment_id,
        )
    )


async def _resolve_rel_type_id(conn, slug: str) -> str | None:
    return await conn.fetchval(
        "SELECT id FROM role_assignment_relationship_types WHERE slug=$1", slug
    )


def _claim_payload(claim: RelationshipClaim) -> dict:
    return {
        "valid_from": claim.valid_from,
        "valid_until": claim.valid_until,
        "notes": claim.notes,
    }


def _validate_window(claim: RelationshipClaim) -> None:
    if (
        claim.valid_from is not None
        and claim.valid_until is not None
        and claim.valid_from > claim.valid_until
    ):
        raise _RelationshipRejected(RelationshipRejectReason.INVALID)


async def check_edge_within_assignments(
    conn: asyncpg.Connection,
    from_assignment_id: str,
    to_assignment_id: str,
    valid_from: date | None,
    valid_until: date | None,
) -> None:
    """Admin-path guard (#301): reject an edge whose *defined* window falls outside
    the intersection of both endpoint assignment windows.

    NULL edge bounds are lenient (unknown, allowed). NULL endpoint bounds are open
    and do not constrain. The observation path does **not** call this — it records
    freely and the daily audit reconciles (mirrors #307)."""
    row = await conn.fetchrow(
        """SELECT GREATEST(f.start_date, t.start_date) AS lo,
                  LEAST(f.end_date, t.end_date)        AS hi
             FROM role_assignments f, role_assignments t
            WHERE f.id=$1 AND t.id=$2""",
        from_assignment_id,
        to_assignment_id,
    )
    if row is None:
        return
    lo, hi = row["lo"], row["hi"]
    if valid_from is not None and lo is not None and valid_from < lo:
        raise EdgeOutsideAssignmentWindow(
            f"valid_from {valid_from} precedes the endpoint window start {lo}"
        )
    if valid_until is not None and hi is not None and valid_until > hi:
        raise EdgeOutsideAssignmentWindow(
            f"valid_until {valid_until} outlasts the endpoint window end {hi}"
        )


# ---------------------------------------------------------------------------
# Apply one claim
# ---------------------------------------------------------------------------


async def _apply_one_relationship(
    conn: asyncpg.Connection, key_id: str | None, claim: RelationshipClaim
) -> RelationshipResult:
    """Apply one relationship claim; return its outcome (never raises for a domain
    rejection — those come back as ``RelationshipResult(REJECTED, reason=...)``)."""
    try:
        if claim.op == "retract":
            return await _retract_relationship(conn, key_id, claim)
        if claim.op != "observe":
            raise _RelationshipRejected(RelationshipRejectReason.INVALID)
        _validate_window(claim)
        if claim.pm_relationship_id is not None:
            return await _refine_relationship_in_place(conn, key_id, claim)
        return await _create_or_refine_natural(conn, key_id, claim)
    except _RelationshipRejected as exc:
        return RelationshipResult(RelationshipDisposition.REJECTED, None, exc.reason)


async def _create_or_refine_natural(
    conn, key_id: str | None, claim: RelationshipClaim
) -> RelationshipResult:
    """Natural-key observe: resolve endpoints + rel_type, match identity → refine
    active row, else anti-resurrect an archived twin, else create."""
    from_id = claim.from_pm_assignment_id
    to_id = claim.to_pm_assignment_id
    if not await _live_assignment_exists(conn, from_id) or not await _live_assignment_exists(
        conn, to_id
    ):
        raise _RelationshipRejected(RelationshipRejectReason.ASSIGNMENT_UNRESOLVED)
    if from_id == to_id:
        raise _RelationshipRejected(RelationshipRejectReason.SELF_RELATIONSHIP)

    rel_type_id = await _resolve_rel_type_id(conn, claim.rel_type)
    if rel_type_id is None:
        raise _RelationshipRejected(RelationshipRejectReason.REL_TYPE_UNKNOWN)

    active = await conn.fetchrow(
        """SELECT id, source_key_id, valid_from, valid_until, notes
             FROM role_assignment_relationships
            WHERE from_assignment_id=$1 AND to_assignment_id=$2 AND rel_type_id=$3
              AND archived_at IS NULL""",
        from_id,
        to_id,
        rel_type_id,
    )
    if active is not None:
        return await _refine_row(conn, active, key_id, claim, id_addressed=False)

    # No active twin. An archived twin means the edge was retracted — a retract is
    # authoritative, so re-observation auto-attaches rather than reviving it
    # (anti-resurrection, mirrors events #322 / citations #319).
    archived_id = await conn.fetchval(
        """SELECT id FROM role_assignment_relationships
            WHERE from_assignment_id=$1 AND to_assignment_id=$2 AND rel_type_id=$3
              AND archived_at IS NOT NULL
            ORDER BY archived_at DESC, id DESC LIMIT 1""",
        from_id,
        to_id,
        rel_type_id,
    )
    if archived_id is not None:
        return RelationshipResult(RelationshipDisposition.AUTO_ATTACHED, archived_id)

    new_id = generate_id()
    await conn.execute(
        """INSERT INTO role_assignment_relationships
               (id, from_assignment_id, to_assignment_id, rel_type_id,
                valid_from, valid_until, notes, source_key_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        new_id,
        from_id,
        to_id,
        rel_type_id,
        claim.valid_from,
        claim.valid_until,
        claim.notes,
        key_id,
    )
    logger.info("Created assignment relationship id=%s %s->%s", new_id, from_id, to_id)
    return RelationshipResult(RelationshipDisposition.NEW, new_id)


async def _refine_relationship_in_place(
    conn, key_id: str | None, claim: RelationshipClaim
) -> RelationshipResult:
    """id-addressed refine: immutable identity, diff-before-write, provenance gate."""
    existing = await conn.fetchrow(
        """SELECT id, from_assignment_id, to_assignment_id, rel_type_id,
                  source_key_id, valid_from, valid_until, notes
             FROM role_assignment_relationships
            WHERE id=$1 AND archived_at IS NULL""",
        claim.pm_relationship_id,
    )
    if existing is None:
        raise _RelationshipRejected(RelationshipRejectReason.RELATIONSHIP_NOT_FOUND)
    await _assert_identity_matches(conn, existing, claim)
    return await _refine_row(conn, existing, key_id, claim, id_addressed=True)


async def _assert_identity_matches(conn, existing, claim: RelationshipClaim) -> None:
    """Identity is immutable — a supplied, differing endpoint / rel_type addresses a
    *different* edge, never a silent reclassify."""
    if (
        claim.from_pm_assignment_id is not None
        and claim.from_pm_assignment_id != existing["from_assignment_id"]
    ):
        raise _RelationshipRejected(RelationshipRejectReason.IDENTITY_IMMUTABLE)
    if (
        claim.to_pm_assignment_id is not None
        and claim.to_pm_assignment_id != existing["to_assignment_id"]
    ):
        raise _RelationshipRejected(RelationshipRejectReason.IDENTITY_IMMUTABLE)
    if claim.rel_type is not None:
        rel_type_id = await _resolve_rel_type_id(conn, claim.rel_type)
        if rel_type_id is not None and rel_type_id != existing["rel_type_id"]:
            raise _RelationshipRejected(RelationshipRejectReason.IDENTITY_IMMUTABLE)


async def _refine_row(
    conn, existing, key_id: str | None, claim: RelationshipClaim, *, id_addressed: bool
) -> RelationshipResult:
    """Shared refine tail for both natural-key and id-addressed observe paths.

    Diff-before-write precedes the provenance gate (an identical redelivery by a
    foreign key stays a quiet no-op, exactly as events / citations refine does)."""
    payload = _claim_payload(claim)
    if all(existing[col] == val for col, val in payload.items()):
        return RelationshipResult(RelationshipDisposition.AUTO_ATTACHED, existing["id"])

    if existing["source_key_id"] is not None and existing["source_key_id"] != key_id:
        logger.warning(
            "relationship refine source mismatch rel=%s owner=%s caller=%s",
            existing["id"],
            existing["source_key_id"],
            key_id,
        )
        raise _RelationshipRejected(RelationshipRejectReason.PROVENANCE_CONFLICT)

    await conn.execute(
        """UPDATE role_assignment_relationships SET
               valid_from=$2, valid_until=$3, notes=$4,
               source_key_id=COALESCE(source_key_id, $5)
           WHERE id=$1 AND archived_at IS NULL""",
        existing["id"],
        claim.valid_from,
        claim.valid_until,
        claim.notes,
        key_id,
    )
    logger.info(
        "Refined assignment relationship id=%s (id_addressed=%s)", existing["id"], id_addressed
    )
    return RelationshipResult(RelationshipDisposition.UPDATED, existing["id"])


async def _retract_relationship(
    conn, key_id: str | None, claim: RelationshipClaim
) -> RelationshipResult:
    """id-addressed void (#322 model): archive so the outbox drops the anchor.

    - no ``pm_relationship_id`` → ``invalid`` (retract is always id-addressed)
    - id unresolved → ``relationship_not_found``
    - already archived → no-op (``auto-attached``, no clock bump), checked before
      the provenance gate so a foreign re-emit stays quiet
    - supplied endpoint / rel_type differs from the stored row → ``identity_immutable``
    - live row, foreign non-NULL ``source_key_id`` → ``provenance_conflict``
    - else archive it → ``retracted``
    """
    if claim.pm_relationship_id is None:
        raise _RelationshipRejected(RelationshipRejectReason.INVALID)

    existing = await conn.fetchrow(
        """SELECT id, from_assignment_id, to_assignment_id, rel_type_id,
                  source_key_id, archived_at
             FROM role_assignment_relationships WHERE id=$1""",
        claim.pm_relationship_id,
    )
    if existing is None:
        raise _RelationshipRejected(RelationshipRejectReason.RELATIONSHIP_NOT_FOUND)
    if existing["archived_at"] is not None:
        return RelationshipResult(RelationshipDisposition.AUTO_ATTACHED, existing["id"])

    await _assert_identity_matches(conn, existing, claim)

    if existing["source_key_id"] is not None and existing["source_key_id"] != key_id:
        logger.warning(
            "relationship retract source mismatch rel=%s owner=%s caller=%s",
            existing["id"],
            existing["source_key_id"],
            key_id,
        )
        raise _RelationshipRejected(RelationshipRejectReason.PROVENANCE_CONFLICT)

    await conn.execute(
        """UPDATE role_assignment_relationships
               SET archived_at = now(), source_key_id = COALESCE(source_key_id, $2)
           WHERE id=$1 AND archived_at IS NULL""",
        existing["id"],
        key_id,
    )
    logger.info("Retracted assignment relationship id=%s", existing["id"])
    return RelationshipResult(RelationshipDisposition.RETRACTED, existing["id"])


async def apply_relationship_observations(
    conn: asyncpg.Connection,
    key_id: str | None,
    claims: list[RelationshipClaim],
) -> list[RelationshipResult]:
    """Apply relationship claims **partial-success** (#301) — the native surface.

    Each claim runs in its own savepoint: a rejection (domain slug or DB
    constraint) rolls back only that claim and is reported alongside the ones that
    landed. **Must run inside the caller's transaction** so the batch shares one
    outer commit.
    """
    results: list[RelationshipResult] = []
    for claim in claims:
        try:
            async with conn.transaction():
                result = await _apply_one_relationship(conn, key_id, claim)
                if result.disposition is RelationshipDisposition.REJECTED:
                    raise _SavepointRollback(result)
        except _SavepointRollback as sr:
            result = sr.result
        except asyncpg.PostgresError:
            # Any per-claim DB error (unique / check / FK) rolls back only this
            # claim's savepoint and is isolated as a rejection so siblings still
            # land — the partial-success contract (mirrors citations #319).
            result = RelationshipResult(
                RelationshipDisposition.REJECTED, None, RelationshipRejectReason.INVALID
            )
        results.append(result)
    return results
