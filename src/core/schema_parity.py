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

from dataclasses import dataclass, field
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
    ``target_only`` is informational (see module docstring). ``kind`` namespaces
    the report (``constraint`` / ``function`` / ``trigger``).
    """

    kind: str
    missing_in_target: list = field(default_factory=list)
    mismatched: list = field(default_factory=list)
    target_only: list = field(default_factory=list)

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


def format_drift_report(drift: SchemaObjectDrift, *, reference: str, target: str) -> str:
    """Human-readable multi-line report of a drift, for the ops-check journal log.

    Object identities are namespaced by kind (``constraint.`` / ``function.`` /
    ``trigger.``) so a combined multi-kind report stays unambiguous.
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
            lines.append(f"      reference: {ref_def}")
            lines.append(f"      target:    {tgt_def}")
    if drift.target_only:
        lines.append(
            f"note: {len(drift.target_only)} {kind}(s) present only in target "
            f"({target}) — not drift (stale reference or pending-removal leftover):"
        )
        lines += [f"  - {kind}.{k.label}" for k in drift.target_only]
    return "\n".join(lines)
