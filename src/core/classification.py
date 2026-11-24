"""Org-kind classification: suggestion sources and operator decisions (#877).

Every source's answer for a classified field is an append-only row in
``classification_suggestions``: ``rules`` (derived from identifiers), ``jev``
(TypeSafe, phase 2) and ``manual`` (an operator). Sources only *suggest*. The
resolved kind, ``organizations.org_kind_id``, is written by
:func:`record_decision` and :func:`carry_kind_on_merge` alone: an operator's
decision, the operator-run bulk accept, or a merge keeping a decision. No
producer, applier or importer path sets it, and
``tests/core/test_classification_write_sweep.py`` holds every other module to
that.

Design: ``docs/plans/2026-11-24-org-kind-suggestions-design.md``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import asyncpg

from src.core.db import generate_id
from src.core.logging import get_logger

logger = get_logger(__name__)

RULES_VERSION = "rules-1"

Action = Literal["accept", "set", "dismiss", "clear"]
ACTIONS: tuple[Action, ...] = ("accept", "set", "dismiss", "clear")
DECIDING_ACTIONS = frozenset({"accept", "set", "dismiss"})
SOURCES = ("rules", "jev", "manual")


class DecisionError(ValueError):
    """An operator decision that can't be recorded; the message is safe to show."""


def rules_kind(implied: Iterable[tuple[str, str]]) -> tuple[str | None, str | None]:
    """Return ``(kind, note)`` from an org's ``(kind, identifier_type_slug)`` pairs.

    The pairs are the org's identifiers whose type implies a kind
    (``entity_identifier_types.implies_org_kind_id``, reference data seeded in
    ``schema.sql``). ``(None, None)``: no rule applies. ``("none", note)``: the
    identifiers point to different kinds, and the note names them.
    """
    found = sorted(set(implied))
    kinds = {k for k, _ in found}
    if not kinds:
        return None, None
    if len(kinds) == 1:
        return kinds.pop(), None
    by_kind: dict[str, str] = {}
    for kind, slug in found:
        by_kind.setdefault(kind, slug)
    return "none", "conflict: " + ", ".join(f"{k} ({s})" for k, s in sorted(by_kind.items()))


_LATEST_SQL = """
SELECT DISTINCT ON (source)
       source, value, manual_action, confidence, model, note, created_by, created_at
FROM classification_suggestions
WHERE entity_type = 'organization' AND entity_id = $1 AND field = 'org_kind'
  AND ignored_at IS NULL
ORDER BY source, created_at DESC, id DESC
"""


async def latest_answers(db: asyncpg.Connection, org_id: str) -> dict[str, asyncpg.Record]:
    """Each source's current answer for an org, keyed by source; ignored rows skipped."""
    return {r["source"]: r for r in await db.fetch(_LATEST_SQL, org_id)}


def is_deciding(manual: asyncpg.Record | None) -> bool:
    """True when a latest ``manual`` row decides: accept, set or dismiss."""
    return manual is not None and manual["manual_action"] in DECIDING_ACTIONS


async def is_decided(db: asyncpg.Connection, org_id: str) -> bool:
    """True when the org's latest manual row is accept, set or dismiss."""
    return is_deciding((await latest_answers(db, org_id)).get("manual"))


async def record_decision(
    db: asyncpg.Connection,
    *,
    org_id: str,
    action: Action,
    kind: str | None = None,
    source: str | None = None,
    user_id: str | None = None,
    note: str | None = None,
) -> None:
    """Record an operator decision on an org's kind, atomically.

    - ``set``: any kind. ``accept``: the kind ``source`` currently answers.
      Both write a ``manual`` row and set ``org_kind_id``.
    - ``dismiss``: decided, with no kind. ``clear``: back to undecided.
      Both clear ``org_kind_id``.

    Raises :class:`DecisionError` for an unknown or archived org, an unknown
    kind, or an ``accept`` whose source no longer answers that kind.
    """
    if action not in ACTIONS:
        raise DecisionError(f"unknown action: {action!r}")
    if action in ("accept", "set") and not kind:
        raise DecisionError(f"{action} needs a kind")
    if action == "accept" and source not in ("rules", "jev"):
        raise DecisionError("accept needs the source whose answer it takes (rules or jev)")
    async with db.transaction():
        locked = await db.fetchval(
            "SELECT id FROM organizations WHERE id=$1 AND archived_at IS NULL FOR UPDATE", org_id
        )
        if locked is None:
            raise DecisionError(f"organization {org_id} not found or archived")
        kind_id = None
        if action in ("accept", "set"):
            kind_id = await db.fetchval("SELECT id FROM org_kinds WHERE slug=$1", kind)
            if kind_id is None:
                raise DecisionError(f"unknown kind: {kind!r}")
        if action == "accept":
            current = (await latest_answers(db, org_id)).get(source)
            if current is None or current["value"] != kind:
                raise DecisionError(f"{source} no longer suggests {kind!r} (stale page?)")
            note = note or f"accepted {source}"
        await db.execute(
            "INSERT INTO classification_suggestions"
            " (id, entity_type, entity_id, field, source, value, manual_action, note, created_by)"
            " VALUES ($1, 'organization', $2, 'org_kind', 'manual', $3, $4, $5, $6)",
            generate_id(),
            org_id,
            kind if action in ("accept", "set") else None,
            action,
            note,
            user_id,
        )
        await db.execute("UPDATE organizations SET org_kind_id=$1 WHERE id=$2", kind_id, org_id)
    logger.info("org kind decision org=%s action=%s kind=%s", org_id, action, kind)


async def carry_kind_on_merge(db: asyncpg.Connection, *, loser_id: str, survivor_id: str) -> None:
    """Merge the loser's classification into the survivor (#877 D2/D3).

    Run inside the merge's transaction, before the loser row goes. The survivor
    keeps its own decision; when it has none, it takes the loser's ``manual``
    rows and resolved kind. The loser's derived ``rules`` and ``jev`` rows are
    deleted: they describe an identity that no longer exists. Survivor rules are
    recomputed by the next rules run, because its identifiers changed.
    """
    survivor_decided = await is_decided(db, survivor_id)
    loser_decision = (await latest_answers(db, loser_id)).get("manual")
    if not survivor_decided and is_deciding(loser_decision):
        await db.execute(
            "UPDATE classification_suggestions SET entity_id=$1"
            " WHERE entity_type='organization' AND entity_id=$2 AND source='manual'",
            survivor_id,
            loser_id,
        )
        # The moved rows keep their timestamps, so a newer `clear` of the
        # survivor's own would still read as latest. Restate the carried decision.
        await db.execute(
            "INSERT INTO classification_suggestions"
            " (id, entity_type, entity_id, field, source, value, manual_action, note)"
            " VALUES ($1, 'organization', $2, 'org_kind', 'manual', $3, $4, $5)",
            generate_id(),
            survivor_id,
            loser_decision["value"],
            loser_decision["manual_action"],
            f"carried from merged {loser_id}",
        )
        await db.execute(
            "UPDATE organizations SET org_kind_id ="
            " (SELECT org_kind_id FROM organizations WHERE id=$2) WHERE id=$1",
            survivor_id,
            loser_id,
        )
    await db.execute(
        "DELETE FROM classification_suggestions WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )


@dataclass(frozen=True)
class RulesChange:
    """One rules row to insert: the org's answer differs from its latest rules row."""

    org_id: str
    value: str
    note: str | None
    previous: str | None


_RULES_INPUT_SQL = """
SELECT o.id AS org_id,
       COALESCE(array_agg(k.slug) FILTER (WHERE k.slug IS NOT NULL), '{}') AS kinds,
       COALESCE(array_agg(t.slug) FILTER (WHERE k.slug IS NOT NULL), '{}') AS type_slugs,
       (SELECT s.value FROM classification_suggestions s
         WHERE s.entity_type = 'organization' AND s.entity_id = o.id
           AND s.field = 'org_kind' AND s.source = 'rules' AND s.ignored_at IS NULL
         ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS previous
FROM organizations o
LEFT JOIN identifiers i ON i.entity_id = o.id
LEFT JOIN entity_identifier_types t
       ON t.id = i.entity_identifier_type_id AND t.entity_type = 'organization'
LEFT JOIN org_kinds k ON k.id = t.implies_org_kind_id
WHERE o.archived_at IS NULL
GROUP BY o.id
ORDER BY o.id
"""


async def plan_rules(db: asyncpg.Connection) -> list[RulesChange]:
    """The rules rows a run would insert, for every active org.

    A row is planned only when the answer changed: a kind where there was none
    or a different one, ``none`` + note on conflicting identifiers, and
    ``none`` ("no rule applies") when an earlier answer has lost its
    identifier. An org with no rule and no earlier answer gets nothing: an
    absent rules row already means "no rule".
    """
    changes = []
    for r in await db.fetch(_RULES_INPUT_SQL):
        kind, note = rules_kind(zip(r["kinds"], r["type_slugs"], strict=True))
        previous = r["previous"]
        if kind is None:
            if previous is None or previous == "none":
                continue
            kind, note = "none", "no rule applies"
        if kind == previous:
            continue
        changes.append(RulesChange(r["org_id"], kind, note, previous))
    return changes


async def apply_rules(db: asyncpg.Connection, changes: Iterable[RulesChange]) -> int:
    """Insert the planned rules rows in one transaction; return how many."""
    rows = [(generate_id(), c.org_id, c.value, RULES_VERSION, c.note) for c in changes]
    if not rows:
        return 0
    async with db.transaction():
        await db.executemany(
            "INSERT INTO classification_suggestions"
            " (id, entity_type, entity_id, field, source, value, model, note)"
            " VALUES ($1, 'organization', $2, 'org_kind', 'rules', $3, $4, $5)",
            rows,
        )
    return len(rows)
