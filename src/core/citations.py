"""Citation write logic — source provenance for a fact (#319).

A citation is human-checkable evidence (url / title / excerpt / accessed_at)
attached to an entity or one of its fields. Distinct from the actor / ingestion /
confidence provenance axes; curated, observable, retractable.

**Identity** = ``(entity_type, entity_id, field_name, url)`` with
``NULLS NOT DISTINCT`` (active rows) — so a NULL ``url`` is one distinct slot:
at most one URL-less citation per ``(entity, field)``. ``title`` / ``excerpt`` /
``accessed_at`` are mutable payload, never identity.

Mirrors the events model (#321/#322):

- **observe** (default) — no ``pm_citation_id`` → match identity → refine that
  active row in place (diff-before-write no-op → ``auto-attached``) else create
  (``new``); ``pm_citation_id`` → id-addressed refine (identity immutable).
- **retract** — ``op="retract"`` archives the id-addressed row (``archived_at``,
  never hard-delete); already-archived is a no-op; anti-resurrection: re-observing
  retracted content auto-attaches to the archived row rather than reviving it.

``field_name`` (non-NULL) is validated against :data:`CITABLE_FIELDS`.

Two transports (like events): :func:`write_citations` is all-or-nothing (embedded
on a parent observation); :func:`apply_citation_observations` is partial-success
(the citation-native surface — per-claim savepoint + disposition + reason slug).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import asyncpg

from src.core.db import generate_id
from src.core.logging import get_logger
from src.core.observation import ObservationRejected

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

CITABLE_ENTITY_TYPES = frozenset(
    {
        "organization",
        "person",
        "role",
        "role_assignment",
        "jurisdiction",
        "person_name",
        "entity_event",
    }
)

# Per-entity-type field allowlist (#319). field_name NULL is always allowed
# (a whole-entity citation); a non-NULL field_name outside its entity's set is
# rejected ``citable_field_unknown`` — prevents a typo'd field from stranding a
# citation that renders against no column. Governed like role-types (#266) /
# statuses (#306): conservative, grows as real citation targets emerge.
CITABLE_FIELDS: dict[str, frozenset[str]] = {
    "organization": frozenset({"notes"}),
    "person": frozenset({"notes"}),
    "role": frozenset({"title", "notes"}),
    "role_assignment": frozenset({"start_date", "end_date", "is_current", "notes"}),
    "jurisdiction": frozenset({"notes"}),
    "person_name": frozenset({"name"}),
    "entity_event": frozenset({"date", "place", "notes"}),
}

# Existence-check table per citable entity type. person_names is queried by id
# only (no visibility filter): a legal_only/hidden name is still citable and no
# display is performed (allow-listed in tests/core/test_visible_names_filter.py).
_EXISTS_TABLE = {
    "organization": "organizations",
    "person": "people",
    "role": "roles",
    "role_assignment": "role_assignments",
    "jurisdiction": "jurisdictions",
    "person_name": "person_names",
    "entity_event": "entity_events",
}


class CitationDisposition(StrEnum):
    """Per-claim outcome of a citation observation (#319)."""

    NEW = "new"
    AUTO_ATTACHED = "auto-attached"  # identity match no-op / anti-resurrection / already-retracted
    UPDATED = "updated"  # refine-in-place changed mutable payload
    RETRACTED = "retracted"  # op=retract archived the row
    REJECTED = "rejected"


class CitationRejectReason(StrEnum):
    """Machine-readable citation rejection reasons (#319).

    Transient (self-heals on a later cycle, ordering-tolerance): ``entity_unresolved``.
    All others terminal.
    """

    ENTITY_UNRESOLVED = "entity_unresolved"  # transient
    IDENTITY_IMMUTABLE = "identity_immutable"
    CITATION_NOT_FOUND = "citation_not_found"
    PROVENANCE_CONFLICT = "provenance_conflict"
    CITABLE_FIELD_UNKNOWN = "citable_field_unknown"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID = "invalid"


@dataclass(frozen=True)
class CitationResult:
    """Outcome of applying one citation claim; the writer's per-claim return unit."""

    disposition: CitationDisposition
    citation_id: str | None = None
    reason: str | None = None  # CitationRejectReason slug on REJECTED, else None


@dataclass
class CitationClaim:
    """One citation claim (transport-agnostic input to the writers).

    ``entity_type`` / ``entity_id`` (the citation *target*) are passed to the
    writer, not carried here. ``op`` is ``observe`` (default) or ``retract``.
    """

    field_name: str | None = None
    url: str | None = None
    title: str | None = None
    excerpt: str | None = None
    accessed_at: datetime | None = None
    op: str = "observe"
    pm_citation_id: str | None = None


class _CitationRejected(Exception):
    """Internal: a per-claim domain rejection carrying a CitationRejectReason slug."""

    def __init__(self, reason: CitationRejectReason) -> None:
        self.reason = reason
        super().__init__(reason)


class _SavepointRollback(Exception):
    """Internal: unwind a per-claim savepoint once its result is decided."""

    def __init__(self, result: CitationResult) -> None:
        self.result = result
        super().__init__()


async def _citable_entity_exists(conn, entity_type: str, entity_id: str) -> bool:
    """True if the citation target resolves to a live row of the given kind."""
    table = _EXISTS_TABLE[entity_type]
    return bool(await conn.fetchval(f"SELECT 1 FROM {table} WHERE id=$1", entity_id))


def _claim_payload(claim: CitationClaim) -> dict:
    return {"title": claim.title, "excerpt": claim.excerpt, "accessed_at": claim.accessed_at}


async def _apply_one_citation(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    key_id: str | None,
    claim: CitationClaim,
) -> CitationResult:
    """Apply one citation claim; return its outcome (never raises for a domain
    rejection — those come back as ``CitationResult(REJECTED, reason=...)``)."""
    if entity_type not in CITABLE_ENTITY_TYPES:
        return CitationResult(CitationDisposition.REJECTED, None, CitationRejectReason.INVALID)
    try:
        if claim.op == "retract":
            return await _retract_citation(conn, entity_type, entity_id, key_id, claim)
        if claim.op != "observe":
            raise _CitationRejected(CitationRejectReason.INVALID)

        # Observe-path validation (shared by refine + create).
        if claim.field_name is not None and claim.field_name not in CITABLE_FIELDS.get(
            entity_type, frozenset()
        ):
            raise _CitationRejected(CitationRejectReason.CITABLE_FIELD_UNKNOWN)
        if claim.url is None and claim.title is None:
            raise _CitationRejected(CitationRejectReason.MISSING_REQUIRED_FIELD)

        if claim.pm_citation_id is not None:
            return await _refine_citation_in_place(conn, entity_type, entity_id, key_id, claim)
        return await _create_or_refine_natural(conn, entity_type, entity_id, key_id, claim)
    except _CitationRejected as exc:
        return CitationResult(CitationDisposition.REJECTED, None, exc.reason)


async def _create_or_refine_natural(
    conn, entity_type: str, entity_id: str, key_id: str | None, claim: CitationClaim
) -> CitationResult:
    """Natural-key observe: match identity → refine active row, else anti-resurrect
    an archived twin, else create. Entity must exist (ordering-tolerance)."""
    if not await _citable_entity_exists(conn, entity_type, entity_id):
        raise _CitationRejected(CitationRejectReason.ENTITY_UNRESOLVED)

    active = await conn.fetchrow(
        """SELECT id, source_key_id, title, excerpt, accessed_at
           FROM citations
           WHERE entity_type=$1 AND entity_id=$2
             AND field_name IS NOT DISTINCT FROM $3
             AND url IS NOT DISTINCT FROM $4
             AND archived_at IS NULL""",
        entity_type,
        entity_id,
        claim.field_name,
        claim.url,
    )
    if active is not None:
        return await _refine_row(conn, active, key_id, claim, id_addressed=False)

    # No active twin. An archived twin means this content was retracted — a
    # retract is authoritative, so re-observation auto-attaches to it rather than
    # minting a fresh active row (anti-resurrection, mirrors events #322).
    archived_id = await conn.fetchval(
        """SELECT id FROM citations
           WHERE entity_type=$1 AND entity_id=$2
             AND field_name IS NOT DISTINCT FROM $3
             AND url IS NOT DISTINCT FROM $4
             AND archived_at IS NOT NULL
           ORDER BY archived_at DESC, id DESC LIMIT 1""",
        entity_type,
        entity_id,
        claim.field_name,
        claim.url,
    )
    if archived_id is not None:
        return CitationResult(CitationDisposition.AUTO_ATTACHED, archived_id)

    new_id = generate_id()
    await conn.execute(
        """INSERT INTO citations
               (id, entity_type, entity_id, field_name, url, title, excerpt,
                accessed_at, source_key_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        new_id,
        entity_type,
        entity_id,
        claim.field_name,
        claim.url,
        claim.title,
        claim.excerpt,
        claim.accessed_at,
        key_id,
    )
    logger.info("Created citation id=%s entity=%s/%s", new_id, entity_type, entity_id)
    return CitationResult(CitationDisposition.NEW, new_id)


async def _refine_citation_in_place(
    conn, entity_type: str, entity_id: str, key_id: str | None, claim: CitationClaim
) -> CitationResult:
    """id-addressed refine: immutable identity, diff-before-write, provenance gate."""
    existing = await conn.fetchrow(
        """SELECT id, field_name, url, source_key_id, title, excerpt, accessed_at
           FROM citations
           WHERE id=$1 AND entity_type=$2 AND entity_id=$3 AND archived_at IS NULL""",
        claim.pm_citation_id,
        entity_type,
        entity_id,
    )
    if existing is None:
        raise _CitationRejected(CitationRejectReason.CITATION_NOT_FOUND)

    # Identity is immutable — a supplied, differing field_name or url addresses a
    # *different* citation, never a silent reclassify.
    if claim.field_name is not None and claim.field_name != existing["field_name"]:
        raise _CitationRejected(CitationRejectReason.IDENTITY_IMMUTABLE)
    if claim.url is not None and claim.url != existing["url"]:
        raise _CitationRejected(CitationRejectReason.IDENTITY_IMMUTABLE)

    return await _refine_row(conn, existing, key_id, claim, id_addressed=True)


async def _refine_row(
    conn, existing, key_id: str | None, claim: CitationClaim, *, id_addressed: bool
) -> CitationResult:
    """Shared refine tail for both natural-key and id-addressed observe paths.

    Diff-before-write precedes the provenance gate (an identical redelivery by a
    foreign key stays a quiet no-op, exactly as the events refine does).
    """
    payload = _claim_payload(claim)
    if all(existing[col] == val for col, val in payload.items()):
        return CitationResult(CitationDisposition.AUTO_ATTACHED, existing["id"])

    if existing["source_key_id"] is not None and existing["source_key_id"] != key_id:
        logger.warning(
            "citation refine source mismatch citation=%s owner=%s caller=%s",
            existing["id"],
            existing["source_key_id"],
            key_id,
        )
        raise _CitationRejected(CitationRejectReason.PROVENANCE_CONFLICT)

    await conn.execute(
        """UPDATE citations SET
               title=$2, excerpt=$3, accessed_at=$4,
               source_key_id=COALESCE(source_key_id, $5)
           WHERE id=$1 AND archived_at IS NULL""",
        existing["id"],
        claim.title,
        claim.excerpt,
        claim.accessed_at,
        key_id,
    )
    logger.info("Refined citation id=%s (id_addressed=%s)", existing["id"], id_addressed)
    return CitationResult(CitationDisposition.UPDATED, existing["id"])


async def _retract_citation(
    conn, entity_type: str, entity_id: str, key_id: str | None, claim: CitationClaim
) -> CitationResult:
    """id-addressed void (#322 model): archive so the outbox drops the anchor.

    - no ``pm_citation_id`` → ``invalid`` (retract is always id-addressed)
    - id unresolved (or belongs to another entity) → ``citation_not_found``
    - already archived → diff-gate no-op (``auto-attached``, no clock bump);
      checked before provenance so a foreign re-emit stays quiet (mirrors refine)
    - supplied field_name/url differs from the stored row → ``identity_immutable``
    - live row, foreign non-NULL ``source_key_id`` → ``provenance_conflict``
    - else archive it → ``retracted``
    """
    if claim.pm_citation_id is None:
        raise _CitationRejected(CitationRejectReason.INVALID)

    existing = await conn.fetchrow(
        """SELECT id, field_name, url, source_key_id, archived_at
           FROM citations WHERE id=$1 AND entity_type=$2 AND entity_id=$3""",
        claim.pm_citation_id,
        entity_type,
        entity_id,
    )
    if existing is None:
        raise _CitationRejected(CitationRejectReason.CITATION_NOT_FOUND)

    if existing["archived_at"] is not None:
        return CitationResult(CitationDisposition.AUTO_ATTACHED, existing["id"])

    if claim.field_name is not None and claim.field_name != existing["field_name"]:
        raise _CitationRejected(CitationRejectReason.IDENTITY_IMMUTABLE)
    if claim.url is not None and claim.url != existing["url"]:
        raise _CitationRejected(CitationRejectReason.IDENTITY_IMMUTABLE)

    if existing["source_key_id"] is not None and existing["source_key_id"] != key_id:
        logger.warning(
            "citation retract source mismatch citation=%s owner=%s caller=%s",
            existing["id"],
            existing["source_key_id"],
            key_id,
        )
        raise _CitationRejected(CitationRejectReason.PROVENANCE_CONFLICT)

    await conn.execute(
        """UPDATE citations
               SET archived_at = now(), source_key_id = COALESCE(source_key_id, $2)
           WHERE id=$1 AND archived_at IS NULL""",
        existing["id"],
        key_id,
    )
    logger.info("Retracted citation id=%s", existing["id"])
    return CitationResult(CitationDisposition.RETRACTED, existing["id"])


async def write_citations(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    key_id: str | None,
    claims: list[CitationClaim],
) -> list[CitationResult]:
    """Apply embedded citation claims **all-or-nothing** (#319).

    The first rejection raises :class:`ObservationRejected` carrying the reason
    slug, rolling the whole (parent) observation back. Returns the per-claim
    results on full success. For per-claim partial-success use
    :func:`apply_citation_observations`.
    """
    results: list[CitationResult] = []
    for claim in claims:
        result = await _apply_one_citation(conn, entity_type, entity_id, key_id, claim)
        if result.disposition is CitationDisposition.REJECTED:
            raise ObservationRejected(result.reason)
        results.append(result)
    return results


async def apply_citation_observations(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    key_id: str | None,
    claims: list[CitationClaim],
) -> list[CitationResult]:
    """Apply citation claims **partial-success** (#319) — the citation-native surface.

    Each claim runs in its own savepoint: a rejection (domain slug or DB
    constraint) rolls back only that claim and is reported alongside the ones that
    landed. **Must run inside the caller's transaction** so the batch shares one
    outer commit.
    """
    results: list[CitationResult] = []
    for claim in claims:
        try:
            async with conn.transaction():
                result = await _apply_one_citation(conn, entity_type, entity_id, key_id, claim)
                if result.disposition is CitationDisposition.REJECTED:
                    raise _SavepointRollback(result)
        except _SavepointRollback as sr:
            result = sr.result
        except (
            asyncpg.CheckViolationError,
            asyncpg.ForeignKeyViolationError,
            asyncpg.UniqueViolationError,
        ):
            result = CitationResult(
                CitationDisposition.REJECTED, None, CitationRejectReason.INVALID
            )
        results.append(result)
    return results
