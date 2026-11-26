"""Person-name observations: per-name results, citations on a name, retract (#845).

The person-name half of ``POST /people/observations``. ``write_names`` (in
``src.core.observation``) still decides what a claim inserts and what it
promotes; this module reports what happened to each claim, attaches a claim's
own citations to the row it resolved to, and lets a key take back a name it
added.

**Retract** follows the other retractable paths (assignments, events,
citations, relationships) where a name allows, and differs in three places:

- **Addressed by identity**, not by a ``pm_*_id``: ``(name, name_type, locale,
  script)`` on the addressed person, the same key ``write_names`` dedups on. It
  is exact, so a producer need not store name ids.
- **Same key only.** The other paths accept a row whose ``source_key_id`` is the
  caller's *or NULL*. Every curated, imported and dataset-applied name carries a
  NULL stamp, so that gate would let any ``observations:write`` key delete them.
- **The row is deleted**, with its citations, as the admin name delete does.
  ``person_names`` has no ``archived_at``. So there is no anti-resurrection: a
  later observe adds the name again.

A retract touches only a **public** row. A row a curator hid is theirs: hiding
is the durable "no" (the dedup probe is visibility-blind, so a re-send stays a
no-op), and deleting the hidden row would forget it.

**What a non-public row reports.** A claim can resolve to a row that is not
public — a deadname (forced ``legal_only`` on insert), or a name a curator hid.
It answers ``not-public`` with no id and no citation results. For a deadname
that is the answer whether the row was new or already held. For any other type
a new row lands public, so ``not-public`` does say the person already holds
that name non-publicly: the dedup probe is visibility-blind, and an observe
followed by a read always showed as much.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import asyncpg

from src.core.ancillary_migrate import delete_citations
from src.core.citations import CitationResult, claims_from, write_citations
from src.core.logging import get_logger
from src.core.observation import ObservationRejected, PersonNameWrite, write_names
from src.core.refusals import ObservationReasonCode

if TYPE_CHECKING:
    from src.api.public.schemas import ObservationPersonName

logger = get_logger(__name__)

__all__ = [
    "NameDisposition",
    "NameRejectReason",
    "NameResult",
    "name_reason_code",
    "write_person_names",
]


class NameDisposition(StrEnum):
    """Per-name outcome of a person-name claim."""

    NEW = "new"
    AUTO_ATTACHED = "auto-attached"
    RETRACTED = "retracted"
    NOT_PUBLIC = "not-public"


class NameRejectReason(StrEnum):
    """Why a name retract rejects its whole observation."""

    PROVENANCE_CONFLICT = "name_provenance_conflict"
    IS_CANONICAL = "name_is_canonical"
    IS_LAST = "name_is_last"
    HAS_READINGS = "name_has_readings"
    HAS_FOREIGN_CITATIONS = "name_has_foreign_citations"


def name_reason_code(reason: NameRejectReason) -> ObservationReasonCode:
    """The stable code for a name refusal (#882): its slug, but one name for the gate.

    ``name_provenance_conflict`` is the same-or-NULL ``source_key_id`` gate every
    other observation calls ``provenance_conflict``; the text keeps the old name.
    """
    if reason is NameRejectReason.PROVENANCE_CONFLICT:
        return ObservationReasonCode.PROVENANCE_CONFLICT
    return ObservationReasonCode(reason.value)


def _refused(reason: NameRejectReason) -> ObservationRejected:
    """The raise for a name refusal: its code, and its slug as the text."""
    return ObservationRejected(name_reason_code(reason), reason.value)


@dataclass(frozen=True)
class NameResult:
    """Outcome of one name claim.

    ``name_id`` is None for ``not-public``, and for a ``retracted`` that found no
    public row to delete. ``citations`` is None when the claim carried none, or
    when the row is not public.
    """

    disposition: NameDisposition
    name_id: str | None = None
    citations: list[CitationResult] | None = None


async def write_person_names(
    conn: asyncpg.Connection,
    person_id: str,
    key_id: str,
    names: "Sequence[ObservationPersonName]",
) -> list[NameResult]:
    """Apply a people observation's name claims; one result per claim, in order.

    **All-or-nothing**, as the embedded events and citations are: a rejected
    retract or name citation raises ``ObservationRejected`` and the caller's
    transaction rolls the observation back.

    Retracts are applied first, then the observes as one ``write_names`` batch —
    its canonical pick ranks the whole payload, and its heal runs even when
    there are none. So a retract is judged against the person as the request
    found them: the heal cannot promote the row about to be retracted, and a
    name added alongside does not lift the last-name guard. The request model
    refuses one identity in both, so list order never matters.
    """
    retracted = {
        i: await _retract(conn, person_id, key_id, n)
        for i, n in enumerate(names)
        if n.op == "retract"
    }
    observed = iter(
        await write_names(
            conn, person_id, "person", key_id, [n for n in names if n.op != "retract"]
        )
    )
    return [
        retracted[i] if i in retracted else await _observed(conn, key_id, n, next(observed))
        for i, n in enumerate(names)
    ]


async def _observed(
    conn: asyncpg.Connection, key_id: str, n: "ObservationPersonName", write: PersonNameWrite
) -> NameResult:
    """Attach the claim's citations to its row and report it."""
    citations = await write_citations(
        conn, "person_name", write.name_id, key_id, claims_from(n.citations)
    )
    if not write.public:
        return NameResult(NameDisposition.NOT_PUBLIC)
    disposition = NameDisposition.NEW if write.is_new else NameDisposition.AUTO_ATTACHED
    return NameResult(disposition, write.name_id, citations or None)


async def _retract(
    conn: asyncpg.Connection, person_id: str, key_id: str, n: "ObservationPersonName"
) -> NameResult:
    """Delete the public row of this identity that the calling key wrote.

    No public row of the identity is a quiet no-op: a re-emitted retract, a row
    a curator retyped or hid. Every other field on the item is ignored.
    """
    rows = await conn.fetch(
        "SELECT id, source_key_id, is_canonical FROM person_names"
        " WHERE person_id=$1 AND name=$2 AND name_type=$3"
        "   AND locale IS NOT DISTINCT FROM $4"
        "   AND script IS NOT DISTINCT FROM $5"
        "   AND visibility = 'public'"
        " ORDER BY id",
        person_id,
        n.name,
        n.name_type,
        n.locale,
        n.script,
    )
    if not rows:
        return NameResult(NameDisposition.RETRACTED)
    own = next((r for r in rows if r["source_key_id"] == key_id), None)
    if own is None:
        raise _refused(NameRejectReason.PROVENANCE_CONFLICT)
    if own["is_canonical"]:
        # A curator promoted it, or a hint claimed the slot: the displayed name.
        raise _refused(NameRejectReason.IS_CANONICAL)
    name_id = own["id"]
    # The last *public* name: the guard answers from what a read shows. Counting
    # hidden rows would tell two people who read alike apart, and let a key take
    # a person's only public name (CR 2).
    if (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND visibility = 'public'",
            person_id,
        )
        == 1
    ):
        raise _refused(NameRejectReason.IS_LAST)
    if await conn.fetchval("SELECT 1 FROM person_names WHERE reading_of_id=$1 LIMIT 1", name_id):
        # reading_of_id is ON DELETE CASCADE: the delete would take rows this
        # key may not have written. A child of any visibility counts, so this
        # answer can reveal that a hidden reading points here — the alternative
        # is cascading a curator's row away unseen.
        raise _refused(NameRejectReason.HAS_READINGS)
    if await conn.fetchval(
        "SELECT 1 FROM citations WHERE entity_type='person_name' AND entity_id=$1"
        "   AND source_key_id IS DISTINCT FROM $2 LIMIT 1",
        name_id,
        key_id,
    ):
        # The gate above covers the row; the citations die with it. One another
        # key or a curator wrote (a retracted one included) is theirs to lose —
        # and evidence someone else put on the name means it is no longer this
        # key's claim alone.
        raise _refused(NameRejectReason.HAS_FOREIGN_CITATIONS)
    # The checks above read a snapshot; a promote, a hide or a delete may commit
    # before this runs. The WHERE re-checks what must still hold — Postgres
    # re-evaluates it against the row as committed — so this never deletes a
    # displayed or hidden name. Not `FOR UPDATE` on the probe: the parent-touch
    # trigger makes a row lock held across statements deadlock-prone here.
    deleted = await conn.fetchval(
        "DELETE FROM person_names"
        " WHERE id=$1 AND NOT is_canonical AND visibility = 'public'"
        " RETURNING id",
        name_id,
    )
    if deleted is None:
        # Promoted, far the likeliest; a hide or a delete lands here too, and a
        # re-sent retract then finds no public row and answers the quiet no-op.
        raise _refused(NameRejectReason.IS_CANONICAL)
    # person_name is citable with no FK (#319), so nothing orders these two.
    await delete_citations(conn, "person_name", name_id)
    logger.info("Retracted person_name id=%s person=%s key=%s", name_id, person_id, key_id)
    return NameResult(NameDisposition.RETRACTED, name_id)
