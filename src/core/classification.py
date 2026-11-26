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

The drain (:func:`drain`, phase 2) runs the sources: rules for every active
org, then Jev for the orgs with no live answer from the pinned model. It runs
in-process (``src/core/classification_drainer.py``), from the CLI and from the
daily timer (``scripts/classify_suggestions.py``), one at a time under a
Postgres advisory lock. It writes suggestions and run rows only.

Design: ``docs/plans/2026-11-24-org-kind-suggestions-design.md``.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import asyncpg
import httpx

from src.core import jev_client as jc
from src.core import org_kind_question as q
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
       -- Zipped by position in plan_rules: both aggregates share one ORDER BY.
       COALESCE(array_agg(k.slug ORDER BY t.slug) FILTER (WHERE k.slug IS NOT NULL), '{}')
           AS kinds,
       COALESCE(array_agg(t.slug ORDER BY t.slug) FILTER (WHERE k.slug IS NOT NULL), '{}')
           AS type_slugs,
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


# --- the drain: rules, then Jev (phase 2) ---------------------------------

# pg_try_advisory_lock key: one drain at a time across workers and processes.
DRAIN_LOCK_KEY = 8770002
# Jev requests in flight at once; answers are stored after each group.
CONCURRENCY = 4
# Consecutive groups with no answer before a run gives up (the API is down).
MAX_DEAD_GROUPS = 2

DrainStatus = Literal["ok", "partial", "failed", "busy", "disabled", "idle"]
Origin = Literal["wake", "tick", "cli", "timer", "reask"]
AskFn = Callable[..., Awaitable[jc.JevAnswer]]


@dataclass
class DrainResult:
    """What one drain did. ``busy``/``disabled``/``idle`` asked nothing."""

    status: DrainStatus
    rules_written: int = 0
    asked: int = 0
    answered: int = 0
    failed: int = 0
    input_tokens: int = 0
    error: str | None = None
    # A failure that asking again won't fix: no key, a 4xx, a malformed reply,
    # another model answering. The timer alerts on one at once (#833).
    permanent: bool = False


_BACKLOG_WHERE = """
FROM organizations o
WHERE o.archived_at IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM classification_suggestions s
      WHERE s.entity_type = 'organization' AND s.entity_id = o.id
        AND s.field = 'org_kind' AND s.source = 'jev'
        AND s.model = $1 AND s.ignored_at IS NULL)
"""
_BACKLOG_SQL = f"SELECT o.id {_BACKLOG_WHERE} ORDER BY o.created_at DESC, o.id LIMIT $2"
_BACKLOG_COUNT_SQL = f"SELECT count(*) {_BACKLOG_WHERE}"

_RETIRE_JEV_SQL = """
UPDATE classification_suggestions SET ignored_at = clock_timestamp()
WHERE entity_type = 'organization' AND entity_id = $1 AND field = 'org_kind'
  AND source = 'jev' AND model = $2 AND ignored_at IS NULL
"""
_INSERT_JEV_SQL = """
INSERT INTO classification_suggestions
    (id, entity_type, entity_id, field, source, value, confidence, probabilities,
     model, input_tokens)
VALUES ($1, 'organization', $2, 'org_kind', 'jev', $3, $4, $5::jsonb, $6, $7)
ON CONFLICT (entity_type, entity_id, field, model)
    WHERE source = 'jev' AND ignored_at IS NULL
DO NOTHING
"""
_INSERT_RUN_SQL = """
INSERT INTO classification_runs
    (id, field, origin, status, model, asked, answered, failed, input_tokens,
     error, started_at)
VALUES ($1, 'org_kind', $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""
_ACTIVE_SQL = """
SELECT o.id FROM organizations o
WHERE o.id = ANY($1::text[]) AND o.archived_at IS NULL
  AND (NOT $2 OR NOT EXISTS (
      SELECT 1 FROM (
          SELECT s.manual_action FROM classification_suggestions s
          WHERE s.entity_type = 'organization' AND s.entity_id = o.id
            AND s.field = 'org_kind' AND s.source = 'manual' AND s.ignored_at IS NULL
          ORDER BY s.created_at DESC, s.id DESC LIMIT 1) latest
      WHERE latest.manual_action IN ('accept', 'set', 'dismiss')))
ORDER BY o.id
"""


async def last_answered_at(db: asyncpg.Connection) -> datetime | None:
    """When a run last stored a Jev answer (any origin), or None."""
    return await db.fetchval(
        "SELECT max(finished_at) FROM classification_runs WHERE field = 'org_kind' AND answered > 0"
    )


async def jev_backlog_count(db: asyncpg.Connection, model: str) -> int:
    """Active orgs with no live Jev answer from *model*."""
    return await db.fetchval(_BACKLOG_COUNT_SQL, model)


async def _store_answers(
    db: asyncpg.Connection, answers: Sequence[tuple[str, jc.JevAnswer]], *, replace: bool
) -> None:
    """Insert one ``jev`` row per answer; with *replace*, retire the live one first."""
    if not answers:
        return
    async with db.transaction():
        for org_id, a in answers:
            if replace:
                await db.execute(_RETIRE_JEV_SQL, org_id, a.model)
            await db.execute(
                _INSERT_JEV_SQL,
                generate_id(),
                org_id,
                a.kind,
                a.confidence,
                json.dumps(a.probabilities) if a.probabilities is not None else None,
                a.model,
                a.input_tokens,
            )


async def _ask_all(
    db: asyncpg.Connection,
    org_ids: Sequence[str],
    *,
    ask: AskFn,
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    replace: bool,
) -> DrainResult:
    """Ask Jev about each org, a few at a time, storing each group's answers."""
    crit = q.criteria(await q.load_kinds(db))
    states = await q.load_states(db, org_ids)
    result = DrainResult(status="ok")
    dead_groups = 0
    for start in range(0, len(org_ids), CONCURRENCY):
        group = org_ids[start : start + CONCURRENCY]
        replies = await asyncio.gather(
            *(ask(states[o], crit, api_key=api_key, model=model, client=client) for o in group),
            return_exceptions=True,
        )
        result.asked += len(group)
        answers: list[tuple[str, jc.JevAnswer]] = []
        stop = False
        for org_id, reply in zip(group, replies, strict=True):
            if isinstance(reply, jc.JevAnswer) and reply.model != model:
                reply = jc.JevError(f"answered by {reply.model}, not the pinned {model}")
            if isinstance(reply, jc.JevAnswer):
                answers.append((org_id, reply))
                result.input_tokens += reply.input_tokens
            elif isinstance(reply, jc.JevError):
                result.failed += 1
                result.error = str(reply)
                result.permanent = result.permanent or not reply.transient
                stop = stop or reply.auth
            else:
                raise reply
        await _store_answers(db, answers, replace=replace)
        result.answered += len(answers)
        dead_groups = 0 if answers else dead_groups + 1
        if stop or dead_groups >= MAX_DEAD_GROUPS:
            break
    if result.failed:
        result.status = "partial" if result.answered else "failed"
    return result


async def _ask_and_record(
    db: asyncpg.Connection,
    org_ids: Sequence[str],
    *,
    origin: Origin,
    ask: AskFn,
    client: httpx.AsyncClient | None,
    replace: bool,
) -> DrainResult:
    """Ask about *org_ids* and write the run row (there is always one here)."""
    started = datetime.now(UTC)
    model = jc.get_model()
    api_key = jc.get_api_key()
    if not api_key:
        result = DrainResult(status="failed", error=f"{jc.API_KEY_ENV} is not set", permanent=True)
    elif client is None:
        async with httpx.AsyncClient() as own:
            result = await _ask_all(
                db, org_ids, ask=ask, client=own, api_key=api_key, model=model, replace=replace
            )
    else:
        result = await _ask_all(
            db, org_ids, ask=ask, client=client, api_key=api_key, model=model, replace=replace
        )
    await db.execute(
        _INSERT_RUN_SQL,
        generate_id(),
        origin,
        result.status,
        model,
        result.asked,
        result.answered,
        result.failed,
        result.input_tokens,
        result.error,
        started,
    )
    if result.failed:
        logger.warning(
            "classification %s: %d/%d Jev answers failed (%s)",
            origin,
            result.failed,
            result.asked,
            result.error,
        )
    return result


async def drain(
    db: asyncpg.Connection,
    *,
    origin: Origin,
    ask: AskFn = jc.ask_kind,
    client: httpx.AsyncClient | None = None,
    limit: int | None = None,
) -> DrainResult:
    """Run rules for every active org, then ask Jev about the backlog.

    One drain at a time (``DRAIN_LOCK_KEY``): a second returns ``busy``. With
    ``JEV_SUGGESTIONS_ENABLED`` off it runs rules only (``disabled``). The
    backlog is the active orgs with no live answer from the pinned model,
    newest first, at most *limit* (default ``JEV_MAX_PER_RUN``).
    """
    if not await db.fetchval("SELECT pg_try_advisory_lock($1)", DRAIN_LOCK_KEY):
        return DrainResult(status="busy")
    try:
        rules_written = await apply_rules(db, await plan_rules(db))
        if not jc.is_enabled():
            return DrainResult(status="disabled", rules_written=rules_written)
        model = jc.get_model()
        rows = await db.fetch(_BACKLOG_SQL, model, limit or jc.max_per_run())
        if not rows:
            return DrainResult(status="idle", rules_written=rules_written)
        result = await _ask_and_record(
            db, [r["id"] for r in rows], origin=origin, ask=ask, client=client, replace=False
        )
        result.rules_written = rules_written
        return result
    finally:
        await db.execute("SELECT pg_advisory_unlock($1)", DRAIN_LOCK_KEY)


async def reask_eligible(
    db: asyncpg.Connection, org_ids: Sequence[str], *, skip_decided: bool
) -> list[str]:
    """The active orgs among *org_ids* (with *skip_decided*, only undecided ones)."""
    return [r["id"] for r in await db.fetch(_ACTIVE_SQL, list(org_ids), skip_decided)]


async def reask(
    db: asyncpg.Connection,
    org_ids: Sequence[str],
    *,
    origin: Origin,
    ask: AskFn = jc.ask_kind,
    client: httpx.AsyncClient | None = None,
    skip_decided: bool = False,
) -> DrainResult:
    """Ask Jev again about *org_ids*, replacing each one's live answer.

    A second opinion: it never touches ``org_kind_id``. Archived orgs are
    skipped, and with *skip_decided* (the CLI refresh) so are orgs whose
    latest manual row decides them. Respects ``JEV_SUGGESTIONS_ENABLED``.
    """
    if not jc.is_enabled():
        return DrainResult(status="disabled")
    ids = await reask_eligible(db, org_ids, skip_decided=skip_decided)
    if not ids:
        return DrainResult(status="idle")
    return await _ask_and_record(db, ids, origin=origin, ask=ask, client=client, replace=True)


# --- health (the admin dashboard card) ------------------------------------


@dataclass(frozen=True)
class JevHealth:
    """Jev's state for the dashboard: config, the last success, failures, spend in tokens."""

    enabled: bool
    key_present: bool
    model: str
    last_success: datetime | None
    backlog: int
    failed_runs_7d: int
    tokens_all: int
    tokens_month: int


_HEALTH_SQL = """
SELECT max(finished_at) FILTER (WHERE answered > 0) AS last_success,
       count(*) FILTER (WHERE failed > 0 AND finished_at >= now() - interval '7 days')
           AS failed_7d,
       COALESCE(sum(input_tokens), 0) AS tokens_all,
       COALESCE(sum(input_tokens) FILTER (
           WHERE finished_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'),
           0) AS tokens_month
FROM classification_runs
WHERE field = 'org_kind'
"""


async def health(db: asyncpg.Connection) -> JevHealth:
    """Read Jev's health: config from the environment, the rest from ``classification_runs``."""
    model = jc.get_model()
    row = await db.fetchrow(_HEALTH_SQL)
    return JevHealth(
        enabled=jc.is_enabled(),
        key_present=bool(jc.get_api_key()),
        model=model,
        last_success=row["last_success"],
        backlog=await jev_backlog_count(db, model),
        failed_runs_7d=row["failed_7d"],
        tokens_all=int(row["tokens_all"]),
        tokens_month=int(row["tokens_month"]),
    )
