"""The producer crosswalk: PM's row scope for an applied dataset (#495).

A producer that publishes snapshots rather than observations needs PM to know
which rows are its own. `source_key_id` cannot answer that (#490 addendum gap
A): `people` and `roles` carry no such column, four of 1,666 organizations do,
and half of usa-wa's assignments predate stamping. So the scope is an explicit
table, seeded from the producer's anchor export, and it doubles as the per-row
handle roles and assignments lose under the identifier collapse (gap B).

Two jobs live here:

* **Parsing** an export. The header is a contract and the ids are Crockford
  base32 ULIDs; a file that fails either is rejected whole rather than
  partially believed, because a short parse silently narrows the applier's
  scope instead of failing it.
* **Resolving** each PM id through merge history. `deleted_entities.merged_into`
  names *that row's* survivor (see :mod:`src.core.merge_signals`), so the walk
  is a chain, not a lookup. Two outcomes never resolve: a tombstone with no
  successor, and an id PM has no record of at all — the latter including every
  merge older than the 90-day tombstone TTL (`scripts/prune_outbox.py`).
  Both belong on the blocking report; guessing is how a seed mints duplicates.
"""

import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

from src.core.db import generate_id

__all__ = [
    "ANCHOR_HEADER",
    "ANCHOR_KINDS",
    "UNRESOLVABLE",
    "Anchor",
    "AnchorFormatError",
    "Resolution",
    "SeedReport",
    "Supersession",
    "UnresolvedAnchor",
    "load_anchors",
    "parse_anchors",
    "resolve_anchor",
    "verify_digest",
]

ANCHOR_HEADER = ("kind", "usa_wa_id", "pm_id")
ANCHOR_KINDS = ("person", "organization", "role", "assignment")

# Crockford base32 excludes I, L, O and U so a transcribed id cannot be
# confused with 1 and 0.
_CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
_ULID_LEN = 26

# usa-wa's kind vocabulary is not PM's tombstone vocabulary: an "assignment" is
# a `role_assignment` on this side.
_TOMBSTONE_TYPE = {
    "person": "person",
    "organization": "organization",
    "role": "role",
    "assignment": "role_assignment",
}
_ENTITY_TABLE = {
    "person": "people",
    "organization": "organizations",
    "role": "roles",
    "assignment": "role_assignments",
}

# The three states an applier must never write through. Stored, reported, and
# left for the triage pass (#501) — never guessed at.
UNRESOLVABLE = ("deleted_no_successor", "missing", "cycle")


class AnchorFormatError(ValueError):
    """The export is not a well-formed anchor file."""


@dataclass(frozen=True)
class Anchor:
    """One `(kind, producer id, PM id)` triple as the producer exported it."""

    kind: str
    producer_id: str
    pm_id: str


@dataclass(frozen=True)
class Resolution:
    """Where an anchor's PM id leads, and whether that is somewhere writable.

    ``status`` is one of ``live``, ``archived``, ``merged``, ``deleted_no_successor``,
    ``missing`` or ``cycle``. ``pm_id`` is the row to use, and is ``None``
    exactly when the anchor is unresolvable.
    """

    status: str
    pm_id: str | None


def _require_ulid(value: str, *, column: str, line: int) -> str:
    if len(value) != _ULID_LEN or not set(value) <= _CROCKFORD:
        raise AnchorFormatError(f"line {line}: {column}={value!r} is not a base32 ULID")
    return value


def parse_anchors(text: str) -> list[Anchor]:
    """Parse an `anchors.csv` export, rejecting the whole file on any bad row."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = tuple(next(reader))
    except StopIteration:
        raise AnchorFormatError(
            f"empty export: expected header {','.join(ANCHOR_HEADER)}"
        ) from None
    if header != ANCHOR_HEADER:
        raise AnchorFormatError(
            f"unexpected header {','.join(header)} — expected {','.join(ANCHOR_HEADER)}"
        )

    anchors: list[Anchor] = []
    seen_keys: set[tuple[str, str]] = set()
    for line, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) != len(ANCHOR_HEADER):
            raise AnchorFormatError(
                f"line {line}: expected {len(ANCHOR_HEADER)} fields, got {len(row)}"
            )
        kind, producer_id, pm_id = row
        if kind not in ANCHOR_KINDS:
            raise AnchorFormatError(f"line {line}: unknown kind {kind!r}")
        # The upsert resolves a repeated key silently (last row wins) while the
        # report still counts both, so the only place this can be seen is here.
        if (kind, producer_id) in seen_keys:
            raise AnchorFormatError(f"line {line}: duplicate anchor for {kind} {producer_id}")
        seen_keys.add((kind, producer_id))
        anchors.append(
            Anchor(
                kind,
                _require_ulid(producer_id, column="usa_wa_id", line=line),
                _require_ulid(pm_id, column="pm_id", line=line),
            )
        )
    return anchors


def verify_digest(data: bytes, expected_sha256: str) -> None:
    """Raise unless ``data`` hashes to ``expected_sha256``.

    A truncated download parses cleanly as a shorter export, so this runs before
    the rows are believed, not after.

    Accepts both the bare hex digest and the published contract's prefixed form
    (``sha256:6d51…``, which is how a catalog entry states it). An algorithm this
    does not compute is refused by name rather than compared as opaque text: the
    latter fails as a content mismatch, which reads as "this file is corrupt"
    when the truth is "nobody checked it".
    """
    algorithm, _, digest = expected_sha256.rpartition(":")
    if algorithm and algorithm.lower() != "sha256":
        raise AnchorFormatError(f"unsupported digest algorithm: {algorithm!r}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest.lower():
        raise AnchorFormatError(f"digest mismatch: expected {digest}, got {actual}")


async def resolve_anchor(db: asyncpg.Connection, kind: str, pm_id: str) -> Resolution:
    """Walk ``pm_id`` through merge history to the row an applier may write to."""
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"unknown anchor kind: {kind!r}")
    table = _ENTITY_TABLE[kind]
    tombstone_type = _TOMBSTONE_TYPE[kind]

    # `seen` both detects a cycle and bounds the walk: ids are finite and each
    # hop consumes one, so no separate hop limit is needed — and a hop limit
    # could only ever fire by reporting a merely long chain as a cycle.
    seen: set[str] = set()
    current = pm_id
    while True:
        if current in seen:
            return Resolution("cycle", None)
        seen.add(current)

        row = await db.fetchrow(f"SELECT archived_at FROM {table} WHERE id = $1", current)  # noqa: S608
        if row is not None:
            status = "archived" if row["archived_at"] is not None else "live"
            if current != pm_id and status == "live":
                status = "merged"
            return Resolution(status, current)

        tombstone = await db.fetchrow(
            "SELECT merged_into FROM deleted_entities WHERE entity_type = $1 AND entity_id = $2",
            tombstone_type,
            current,
        )
        if tombstone is None:
            return Resolution("missing", None)
        if tombstone["merged_into"] is None:
            return Resolution("deleted_no_successor", None)
        current = tombstone["merged_into"]


@dataclass(frozen=True)
class UnresolvedAnchor:
    """An anchor the seed refused to resolve, with the reason it refused."""

    anchor: Anchor
    status: str


@dataclass(frozen=True)
class Supersession:
    """An archived anchor and the live rows that could have absorbed it.

    Carries the anchor, not just the archived id: #501 works from producer ids,
    and a report that names only PM ids makes the operator re-join to find out
    whose anchor broke.
    """

    anchor: Anchor
    archived_pm_id: str
    live_siblings: list[str]


@dataclass
class SeedReport:
    """What a seed run found — the first half of the triage pass (#501)."""

    counts: dict[str, int] = field(default_factory=dict)
    unresolved: list[UnresolvedAnchor] = field(default_factory=list)
    collisions: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    supersessions: list[Supersession] = field(default_factory=list)
    stale: list[tuple[str, str]] = field(default_factory=list)

    # False when the crosswalk table does not exist yet, so an empty `stale` is
    # never read as "nothing is stale" when the truth is "nobody looked".
    stale_checked: bool = True

    @property
    def is_blocking(self) -> bool:
        """True when a human has to look before the applier may run.

        Both conditions are disagreements about identity, which is the one thing
        a diff-applier cannot resolve on its own: an anchor leading nowhere, and
        two producer entities that PM has already merged into one.
        """
        return bool(self.unresolved or self.collisions)


# An archived assignment whose person+role still carries a live row is the shape
# PM's duplicate audit leaves behind: the narrow span is archived and a deepened
# one is kept under a **new** ULID the producer has never seen. That is a merge
# in all but name, and `archived_at` writes no `deleted_entities` row, so the
# merge-chain walk cannot follow it. Reported, never applied: which live sibling
# absorbs the anchor is a judgement (one prod case has three, spanning different
# eras), and guessing it would write a tenure onto the wrong row.
_SUPERSESSION_SQL = """
SELECT live.id
FROM role_assignments archived
JOIN role_assignments live
  ON live.person_id = archived.person_id
 AND live.role_id   = archived.role_id
 AND live.id       <> archived.id
 AND live.archived_at IS NULL
WHERE archived.id = $1
ORDER BY live.start_date NULLS LAST, live.id
"""

_UPSERT_SQL = """
INSERT INTO producer_crosswalk (
    id, source, kind, producer_id, exported_pm_id, pm_id, resolution,
    export_generated_at, export_sha256
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (source, kind, producer_id) DO UPDATE SET
    exported_pm_id      = EXCLUDED.exported_pm_id,
    pm_id               = EXCLUDED.pm_id,
    resolution          = EXCLUDED.resolution,
    export_generated_at = EXCLUDED.export_generated_at,
    export_sha256       = EXCLUDED.export_sha256
"""


async def load_anchors(
    db: asyncpg.Connection,
    source: str,
    anchors: list[Anchor],
    *,
    execute: bool,
    export_generated_at: datetime | None = None,
    export_sha256: str | None = None,
) -> SeedReport:
    """Resolve every anchor and, when ``execute``, seed the crosswalk with it.

    Resolution happens either way: the report is the point of a dry run, and it
    is identical to the one the real run produces.

    **The caller owns the transaction.** With ``execute=True`` this writes row by
    row and opens nothing of its own, so a caller that does not wrap it leaves a
    partially seeded scope behind on any mid-run failure — the one state this
    design exists to prevent. :func:`scripts.seed_producer_crosswalk.seed` is the
    intended door and supplies it.
    """
    report = SeedReport()
    landed: dict[tuple[str, str], list[str]] = defaultdict(list)

    for anchor in anchors:
        resolution = await resolve_anchor(db, anchor.kind, anchor.pm_id)
        report.counts[resolution.status] = report.counts.get(resolution.status, 0) + 1

        if resolution.status in UNRESOLVABLE:
            report.unresolved.append(UnresolvedAnchor(anchor, resolution.status))
        else:
            landed[(anchor.kind, resolution.pm_id)].append(anchor.producer_id)

        if resolution.status == "archived" and anchor.kind == "assignment":
            siblings = [r["id"] for r in await db.fetch(_SUPERSESSION_SQL, resolution.pm_id)]
            if siblings:
                report.supersessions.append(Supersession(anchor, resolution.pm_id, siblings))

        if execute:
            await db.execute(
                _UPSERT_SQL,
                generate_id(),
                source,
                anchor.kind,
                anchor.producer_id,
                anchor.pm_id,
                resolution.pm_id,
                resolution.status,
                export_generated_at,
                export_sha256,
            )

    report.collisions = {key: ids for key, ids in landed.items() if len(ids) > 1}

    # An export that shrinks is ordinary — usa-wa retires an id and stops
    # exporting it. The row stays (retiring it is triage's call, not the seed's),
    # but saying nothing would leave a retired producer id inside the applier's
    # scope indefinitely.
    present = {(a.kind, a.producer_id) for a in anchors}
    try:
        seeded = await db.fetch(
            "SELECT kind, producer_id FROM producer_crosswalk WHERE source = $1"
            " ORDER BY kind, producer_id",
            source,
        )
    except asyncpg.exceptions.UndefinedTableError:
        # Dry-running against a database the schema has not reached yet is a
        # supported and useful thing to do — the resolution above reads only the
        # live entity tables, so it is real. What cannot be checked is announced
        # rather than passed: an empty `stale` here would otherwise mean
        # "nothing is stale" when it means "nobody looked".
        report.stale_checked = False
        return report
    report.stale = [
        (r["kind"], r["producer_id"])
        for r in seeded
        if (r["kind"], r["producer_id"]) not in present
    ]
    return report
