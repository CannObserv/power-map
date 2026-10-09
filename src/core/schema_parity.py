"""Schema-object parity comparison between two live databases (#315, #331).

``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so a ``CHECK`` /
``CONSTRAINT`` / ``REFERENCES … ON DELETE`` modifier added inline *after* a
table first shipped never reaches a DB whose table predates it. This drift
class bit #307→#312 (missing CHECKs) and #315 (an FK stuck at NO ACTION while
the code said SET NULL). Both were caught only by a manual ``pg_constraint``
sweep, after they had already sat in prod.

The same silent-drift window exists for **functions and triggers** (#331): they
are all ``CREATE OR REPLACE`` and self-heal on the next ``apply_schema`` (every
``systemctl restart power-map``), but between a partial apply / hand-applied
hotfix and that restart, prod can run a stale body undetected. The change-feed
trigger surface (~a dozen ``touch_parent_*`` functions + ``trg_touch_entity_*``
triggers driving ``entity_changes``) is now load-bearing enough to guard.

This module is the reusable core of that sweep: snapshot every constraint,
function, and trigger on a DB as ``{key: def}`` and diff a *reference* snapshot
against a *target*. The full server-normalised definition is compared — not mere
presence — so FK ``ON DELETE`` actions, CHECK bodies, and function/trigger
bodies are all in scope (the #315 FK differed only in its action):

* constraints — ``pg_get_constraintdef`` keyed on ``(table, name)``
* functions — ``pg_get_functiondef`` keyed on signature (``name(arg types)``,
  so overloads stay distinct); extension-owned and non-plain (aggregate/window/
  procedure) functions are excluded — the schema installs ``pg_trgm`` / ``vector``
  / ``unaccent`` into ``public``, whose hundreds of functions are not ours to
  guard and would swamp the diff / false-positive on any PG-version skew
* triggers — ``pg_get_triggerdef`` keyed on ``(table, name)``, internal
  (constraint/FK-enforcement) and extension-owned triggers excluded

Contract of the guard (``scripts/audit_schema_constraint_parity.py``): the
*target* (prod) must carry everything the *reference* has, with identical
definitions. An object present only in the target is surfaced for visibility
but is **not** a failure — it usually means the reference is stale or the target
carries a pending-removal leftover (or a hand-applied prod-only object worth
seeing), none of which is the drift we guard.

Reference ahead (#632): worktrees apply their schema to the shared test DB
(``apply-schema.sh --test``), which is also the default reference, before their
PR deploys. An object missing in the target is therefore classified against the
**deployed** ``schema.sql`` (``DeployedSchema``): one it declares is real drift;
one it does not is the reference running ahead of the deploy — a pending deploy
or a stray branch's apply — reported but not failed on. Matching is by name:
``CREATE [OR REPLACE] FUNCTION|TRIGGER name`` for functions and triggers. Most
constraints never appear in ``schema.sql`` at all — Postgres names an inline
``PRIMARY KEY`` / ``REFERENCES`` / ``CHECK`` / ``UNIQUE`` itself — so a constraint
counts as declared when its name is written there, or when it has Postgres'
implicit shape (``<table>_<columns>_<pkey|fkey|key|check|excl|not_null>[N]``) on
a created table whose column words all appear. ``--`` comments are stripped
first, so a name only mentioned in one is not declared. The column test is
schema-wide, not per table, so a new constraint on an existing column (or on a
new column whose name is used elsewhere) still reads as drift until it deploys.
Every ambiguity resolves to "declared": misreading drift as a pending deploy is
the error that hides something, so it is the one the rule avoids. Mismatched
definitions are not classified — a branch that changes a body still reads as
drift until it deploys.

PG-version note: ``pg_get_functiondef`` / ``pg_get_triggerdef`` are deterministic
on a *given* server version but their formatting can legitimately differ across
majors. Two DBs applying the same ``schema.sql`` on the same major produce
byte-identical defs (no whitespace normalisation needed); the guard skips the
function/trigger diff on a major mismatch rather than misreport a version
artifact as body drift. ``pg_get_constraintdef`` is version-stable, so the
constraint diff always runs.

Reference fidelity: the strongest reference is a database built from an *empty*
schema via ``apply_schema`` (its real ``CREATE TABLE`` runs every inline
constraint, including those with no reconciliation ``DO`` block). Where that is
unavailable, any current schema DB works as the reference, with one residual
gap — an object that drifted *identically* in both reference and target is
invisible to a pairwise diff. The per-constraint drop-reapply harness
(``tests/core/test_schema_constraint_migrations.py``) covers the reconciled
subset independently of any live reference.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import NamedTuple

import asyncpg

# Snapshot keys are hashable, orderable NamedTuples each exposing a ``label``
# property (its human-readable identity, namespaced by kind in the report).


class ConstraintKey(NamedTuple):
    """Identity of a constraint: its table and name (schema-local, ``public``)."""

    table: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.table}.{self.name}"


class FunctionKey(NamedTuple):
    """Identity of a function: its signature ``name(identity arg types)``.

    The signature — not the bare name — is the key so overloads stay distinct.
    ``pg_get_function_identity_arguments`` yields the arg types alone (no names
    or defaults), which is exactly the overload-resolution identity.
    """

    signature: str

    @property
    def label(self) -> str:
        return self.signature


class TriggerKey(NamedTuple):
    """Identity of a trigger: its table and name (trigger names are per-table)."""

    table: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.table}.{self.name}"


#: All constraints on user tables in ``public``, keyed for full-definition diff.
#: ``pg_get_constraintdef`` normalises the definition identically on both sides,
#: so equal strings mean genuinely equal constraints (CHECK bodies, FK actions,
#: UNIQUE/PK column lists) — see module docstring on why presence alone is not
#: enough (#315's FK differed only in its ON DELETE action).
_CONSTRAINT_SNAPSHOT_SQL = """
SELECT t.relname AS table_name,
       c.conname AS constraint_name,
       pg_get_constraintdef(c.oid) AS def
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relkind IN ('r', 'p')  -- ordinary + partitioned parents (none today; future-proof)
ORDER BY t.relname, c.conname
"""


#: Plain user functions in ``public``, keyed by signature for full-body diff.
#: ``prokind = 'f'`` excludes aggregates/windows/procedures (``pg_get_functiondef``
#: errors on non-'f'); the ``pg_depend deptype='e'`` anti-join excludes
#: extension-owned functions (``pg_trgm`` / ``vector`` / ``unaccent`` install
#: hundreds into ``public``) — not ours to guard, and a magnet for PG-version
#: false positives. See module docstring.
_FUNCTION_SNAPSHOT_SQL = """
SELECT p.proname AS name,
       pg_get_function_identity_arguments(p.oid) AS args,
       pg_get_functiondef(p.oid) AS def
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.prokind = 'f'
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.objid = p.oid AND d.deptype = 'e'
  )
ORDER BY p.proname, args
"""


#: User triggers on ``public`` tables, keyed on ``(table, name)`` for full-def
#: diff. ``NOT tgisinternal`` drops the implicit FK/constraint-enforcement
#: triggers Postgres creates; the extension anti-join mirrors the function sweep.
_TRIGGER_SNAPSHOT_SQL = """
SELECT t.relname AS table_name,
       tg.tgname AS trigger_name,
       pg_get_triggerdef(tg.oid) AS def
FROM pg_trigger tg
JOIN pg_class t ON t.oid = tg.tgrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND NOT tg.tgisinternal
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.objid = tg.oid AND d.deptype = 'e'
  )
ORDER BY t.relname, tg.tgname
"""


#: Kinds whose ``pg_get_*def`` formatting can legitimately differ across PG
#: majors — the parity guard skips them on a major mismatch (see module docstring).
#: Constraints are version-stable and are not listed, so they always diff.
VERSION_SENSITIVE_KINDS: frozenset[str] = frozenset({"function", "trigger"})


@dataclass(frozen=True)
class SchemaObjectDrift:
    """Result of diffing a reference snapshot against a target, for one kind.

    ``missing_in_target`` and ``mismatched`` are the drift the guard fails on;
    ``target_only`` is informational, and so is ``reference_ahead`` — reference
    objects missing in the target that the deployed ``schema.sql`` does not
    declare, filled only by ``classify_missing`` (see module docstring). ``kind``
    namespaces the report (``constraint`` / ``function`` / ``trigger``).
    """

    kind: str
    missing_in_target: list = field(default_factory=list)
    mismatched: list = field(default_factory=list)
    target_only: list = field(default_factory=list)
    reference_ahead: list = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """True when the target is missing, or disagrees on, any reference object."""
        return bool(self.missing_in_target or self.mismatched)

    @property
    def drift_count(self) -> int:
        """Number of failing objects (missing + mismatched); ``target_only`` excluded."""
        return len(self.missing_in_target) + len(self.mismatched)


async def snapshot_constraints(conn: asyncpg.Connection) -> dict[ConstraintKey, str]:
    """Return ``{(table, name): constraint_def}`` for every constraint on ``conn``."""
    rows = await conn.fetch(_CONSTRAINT_SNAPSHOT_SQL)
    return {ConstraintKey(table=r["table_name"], name=r["constraint_name"]): r["def"] for r in rows}


async def snapshot_functions(conn: asyncpg.Connection) -> dict[FunctionKey, str]:
    """Return ``{signature: function_def}`` for every plain user function on ``conn``.

    Signature is ``name(identity arg types)`` so overloads are distinct keys;
    extension-owned and non-plain functions are excluded (see module docstring).
    """
    rows = await conn.fetch(_FUNCTION_SNAPSHOT_SQL)
    return {FunctionKey(signature=f"{r['name']}({r['args']})"): r["def"] for r in rows}


async def snapshot_triggers(conn: asyncpg.Connection) -> dict[TriggerKey, str]:
    """Return ``{(table, name): trigger_def}`` for every user trigger on ``conn``."""
    rows = await conn.fetch(_TRIGGER_SNAPSHOT_SQL)
    return {TriggerKey(table=r["table_name"], name=r["trigger_name"]): r["def"] for r in rows}


def diff_defs(
    *,
    kind: str,
    reference: dict,
    target: dict,
) -> SchemaObjectDrift:
    """Diff a reference snapshot against a target; keys sorted for stable output.

    Kind-agnostic: works on any ``{key: def}`` snapshot (constraints, functions,
    triggers). Drift = any reference object absent from the target
    (``missing_in_target``) or present with a different definition
    (``mismatched``, carrying reference and target defs). Objects only in the
    target are reported as ``target_only`` but never count as drift.
    """
    missing = sorted(k for k in reference if k not in target)
    mismatched = sorted(
        (k, reference[k], target[k]) for k in reference if k in target and reference[k] != target[k]
    )
    target_only = sorted(k for k in target if k not in reference)
    return SchemaObjectDrift(
        kind=kind,
        missing_in_target=missing,
        mismatched=mismatched,
        target_only=target_only,
    )


#: ``CREATE [OR REPLACE] FUNCTION [public.]name(`` — the name a function is declared by.
_CREATE_FUNCTION_RE = re.compile(
    r'\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:"?public"?\.)?"?(\w+)"?\s*\(',
    re.IGNORECASE,
)
#: ``CREATE [OR REPLACE] [CONSTRAINT] TRIGGER name``, top level or inside a ``DO`` block.
_CREATE_TRIGGER_RE = re.compile(
    r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+"?(\w+)"?', re.IGNORECASE
)
#: ``CREATE [UNLOGGED] TABLE [IF NOT EXISTS] [public.]name``.
_CREATE_TABLE_RE = re.compile(
    r'\bCREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"?public"?\.)?"?(\w+)"?',
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A single-quoted literal (group 1, kept) or a ``--`` comment (dropped), scanned
#: left to right so ``'--execute'`` stays a string and ``-- don't`` stays a comment.
_STRING_OR_COMMENT_RE = re.compile(r"('(?:[^']|'')*')|--[^\n]*")
#: Suffixes Postgres gives a constraint it names itself, before any collision number.
_IMPLICIT_SUFFIXES = ("pkey", "fkey", "key", "check", "excl", "not_null")
#: Postgres truncates identifiers to NAMEDATALEN - 1 bytes; a name this long may have
#: lost its suffix or column words to truncation, so its shape cannot be read.
_MAX_IDENTIFIER_BYTES = 63


def strip_line_comments(sql: str) -> str:
    """``sql`` without its ``--`` comments; a ``--`` inside a string literal stays."""
    return _STRING_OR_COMMENT_RE.sub(lambda m: m.group(1) or "", sql)


def _segments_into_words(parts: list[str], words: frozenset[str]) -> bool:
    """True when ``parts`` rejoin, ``_`` within each run, into a sequence of ``words``.

    An implicit name joins its column names with ``_`` and a column name may hold
    ``_`` itself, so ``first_name_last_name`` has to be read as some split of its
    parts into known words — ``first_name`` + ``last_name`` here.
    """
    reachable = [True] + [False] * len(parts)
    for start in range(len(parts)):
        if not reachable[start]:
            continue
        for end in range(start + 1, len(parts) + 1):
            if "_".join(parts[start:end]) in words:
                reachable[end] = True
    return reachable[-1]


@dataclass(frozen=True)
class DeployedSchema:
    """What the deployed ``schema.sql`` declares, read from its text (#632).

    Built with ``from_sql``; ``declares`` answers whether a snapshot key is one the
    deployed schema creates. See the module docstring for the matching rules and
    why every ambiguity reads as declared.
    """

    functions: frozenset[str]
    triggers: frozenset[str]
    tables: frozenset[str]
    words: frozenset[str]

    @classmethod
    def from_sql(cls, sql: str) -> "DeployedSchema":
        """Index the function, trigger and table names ``sql`` creates, and its words.

        ``--`` comments go first: a name a comment mentions is not one the schema
        declares. The strip is quote-aware, since cutting a line at a ``--`` inside
        a string would lose its later words — and a lost column word reads a
        declared constraint as ahead, the direction that hides drift.
        """
        sql = strip_line_comments(sql)

        def names(pattern: re.Pattern[str]) -> frozenset[str]:
            return frozenset(m.lower() for m in pattern.findall(sql))

        return cls(
            functions=names(_CREATE_FUNCTION_RE),
            triggers=names(_CREATE_TRIGGER_RE),
            tables=names(_CREATE_TABLE_RE),
            words=names(_WORD_RE),
        )

    @property
    def is_empty(self) -> bool:
        """True when no table is created: a blank or wrong file, not a schema."""
        return not self.tables

    def declares(self, kind: str, key: NamedTuple) -> bool:
        """True when the deployed schema creates the object ``key`` names."""
        if kind == "function":
            return key.signature.split("(", 1)[0].lower() in self.functions
        if kind == "trigger":
            return key.name.lower() in self.triggers
        if kind == "constraint":
            return self._declares_constraint(key.table.lower(), key.name.lower())
        raise ValueError(f"unknown schema object kind: {kind!r}")

    def _declares_constraint(self, table: str, name: str) -> bool:
        if name in self.words:
            return True
        if table not in self.tables:
            return False
        if len(name.encode()) >= _MAX_IDENTIFIER_BYTES:
            return True
        suffixes = "|".join(_IMPLICIT_SUFFIXES)
        shape = re.fullmatch(rf"{re.escape(table)}(?:_(.+?))?_(?:{suffixes})\d*", name)
        if shape is None:
            return False
        columns = shape.group(1)
        return columns is None or _segments_into_words(columns.split("_"), self.words)


def classify_missing(drift: SchemaObjectDrift, deployed: DeployedSchema) -> SchemaObjectDrift:
    """Move each missing object the deployed schema does not declare to ``reference_ahead``.

    What stays in ``missing_in_target`` is real drift; what moves is the reference
    running ahead of the deploy (see module docstring). Mismatches and
    target-only objects pass through unchanged.
    """
    missing = [k for k in drift.missing_in_target if deployed.declares(drift.kind, k)]
    ahead = [k for k in drift.missing_in_target if not deployed.declares(drift.kind, k)]
    return replace(drift, missing_in_target=missing, reference_ahead=ahead)


def advance_streaks(
    previous: Mapping[str, Mapping], ahead: Iterable[str], *, today: date
) -> dict[str, dict]:
    """Consecutive-run streaks for the labels ahead this run, from the last run's.

    A streak is ``{"runs": n, "last_day": "YYYY-MM-DD"}``. A label still ahead
    gains a run, a new one starts at 1, and a label no longer ahead drops out, so
    it restarts if it ever comes back. At most one run counts per UTC day: the
    timer is daily, and a manual re-run must not hurry an escalation along.
    """
    day = today.isoformat()
    streaks = {}
    for label in ahead:
        last = previous.get(label)
        if last is not None and last["last_day"] == day:
            streaks[label] = dict(last)
        else:
            streaks[label] = {"runs": (last["runs"] if last else 0) + 1, "last_day": day}
    return streaks


#: Column where the ``reference:`` / ``target:`` def values start (6-space bullet
#: indent + the 11-char ``reference: `` / ``target:    `` label). Multi-line
#: function/trigger bodies indent their continuation lines to here so they stay
#: aligned under the first line instead of spilling to column 0.
_DEF_CONTINUATION_INDENT = " " * 17


def _indent_def(text: str) -> str:
    """Indent every line after the first of a (possibly multi-line) def value."""
    return text.replace("\n", "\n" + _DEF_CONTINUATION_INDENT)


def format_drift_report(drift: SchemaObjectDrift, *, reference: str, target: str) -> str:
    """Human-readable multi-line report of a drift, for the ops-check journal log.

    Object identities are namespaced by kind (``constraint.`` / ``function.`` /
    ``trigger.``) so a combined multi-kind report stays unambiguous. Multi-line
    function/trigger bodies have their continuation lines indented to align under
    the ``reference:`` / ``target:`` label rather than spilling to column 0.
    """
    kind = drift.kind
    lines: list[str] = []
    if drift.missing_in_target:
        lines.append(
            f"{len(drift.missing_in_target)} {kind}(s) present in reference "
            f"({reference}) but MISSING in target ({target}):"
        )
        lines += [f"  - {kind}.{k.label}" for k in drift.missing_in_target]
    if drift.mismatched:
        lines.append(
            f"{len(drift.mismatched)} {kind}(s) with a DIFFERENT definition in target ({target}):"
        )
        for key, ref_def, tgt_def in drift.mismatched:
            lines.append(f"  - {kind}.{key.label}")
            lines.append(f"      reference: {_indent_def(ref_def)}")
            lines.append(f"      target:    {_indent_def(tgt_def)}")
    if drift.reference_ahead:
        lines.append(
            f"note: {len(drift.reference_ahead)} {kind}(s) present in reference "
            f"({reference}) but not in target ({target}) nor the deployed schema.sql — "
            "reference ahead of the deployed schema (pending deploy?):"
        )
        lines += [f"  - {kind}.{k.label}" for k in drift.reference_ahead]
    if drift.target_only:
        lines.append(
            f"note: {len(drift.target_only)} {kind}(s) present only in target "
            f"({target}) — not drift (stale reference or pending-removal leftover):"
        )
        lines += [f"  - {kind}.{k.label}" for k in drift.target_only]
    return "\n".join(lines)
