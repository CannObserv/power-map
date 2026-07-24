"""Constraint-parity comparison between two live databases (issue #315).

``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so a ``CHECK`` /
``CONSTRAINT`` / ``REFERENCES … ON DELETE`` modifier added inline *after* a
table first shipped never reaches a DB whose table predates it. This drift
class bit #307→#312 (missing CHECKs) and #315 (an FK stuck at NO ACTION while
the code said SET NULL). Both were caught only by a manual ``pg_constraint``
sweep, after they had already sat in prod.

This module is the reusable core of that sweep: snapshot every constraint on a
DB as ``{(table, name): pg_get_constraintdef(...)}`` and diff a *reference*
snapshot against a *target*. The full ``pg_get_constraintdef`` is compared —
not mere presence — so FK ``ON DELETE`` actions and CHECK clause bodies are in
scope, not just constraint names (the #315 FK differed only in its action).

Contract of the guard (``scripts/audit_schema_constraint_parity.py``): the
*target* (prod) must carry everything the *reference* has, with identical
definitions. A constraint present only in the target is surfaced for visibility
but is **not** a failure — it usually means the reference is stale or the target
carries a pending-removal leftover, neither of which is the drift we guard.

Reference fidelity: the strongest reference is a database built from an *empty*
schema via ``apply_schema`` (its real ``CREATE TABLE`` runs every inline
constraint, including those with no reconciliation ``DO`` block). Where that is
unavailable, any current schema DB works as the reference, with one residual
gap — a constraint that drifted *identically* in both reference and target is
invisible to a pairwise diff. The per-constraint drop-reapply harness
(``tests/core/test_schema_constraint_migrations.py``) covers the reconciled
subset independently of any live reference.
"""

from dataclasses import dataclass, field
from typing import NamedTuple

import asyncpg


class ConstraintKey(NamedTuple):
    """Identity of a constraint: its table and name (schema-local, ``public``)."""

    table: str
    name: str


#: All constraints on user tables in ``public``, keyed for full-definition diff.
#: ``pg_get_constraintdef`` normalises the definition identically on both sides,
#: so equal strings mean genuinely equal constraints (CHECK bodies, FK actions,
#: UNIQUE/PK column lists) — see module docstring on why presence alone is not
#: enough (#315's FK differed only in its ON DELETE action).
_SNAPSHOT_SQL = """
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


@dataclass(frozen=True)
class ConstraintDrift:
    """Result of diffing a reference constraint snapshot against a target.

    ``missing_in_target`` and ``mismatched`` are the drift the guard fails on;
    ``target_only`` is informational (see module docstring).
    """

    missing_in_target: list[ConstraintKey] = field(default_factory=list)
    mismatched: list[tuple[ConstraintKey, str, str]] = field(default_factory=list)
    target_only: list[ConstraintKey] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """True when the target is missing, or disagrees on, any reference constraint."""
        return bool(self.missing_in_target or self.mismatched)


async def snapshot_constraints(conn: asyncpg.Connection) -> dict[ConstraintKey, str]:
    """Return ``{(table, name): constraint_def}`` for every constraint on ``conn``."""
    rows = await conn.fetch(_SNAPSHOT_SQL)
    return {ConstraintKey(table=r["table_name"], name=r["constraint_name"]): r["def"] for r in rows}


def diff_constraints(
    *,
    reference: dict[ConstraintKey, str],
    target: dict[ConstraintKey, str],
) -> ConstraintDrift:
    """Diff a reference snapshot against a target; keys sorted for stable output.

    Drift = any reference constraint absent from the target (``missing_in_target``)
    or present with a different definition (``mismatched``, carrying reference and
    target defs). Constraints only in the target are reported as ``target_only``
    but never count as drift.
    """
    missing = sorted(k for k in reference if k not in target)
    mismatched = sorted(
        (k, reference[k], target[k]) for k in reference if k in target and reference[k] != target[k]
    )
    target_only = sorted(k for k in target if k not in reference)
    return ConstraintDrift(
        missing_in_target=missing,
        mismatched=mismatched,
        target_only=target_only,
    )


def format_drift_report(drift: ConstraintDrift, *, reference: str, target: str) -> str:
    """Human-readable multi-line report of a drift, for the ops-check journal log."""
    lines: list[str] = []
    if drift.missing_in_target:
        lines.append(
            f"{len(drift.missing_in_target)} constraint(s) present in reference "
            f"({reference}) but MISSING in target ({target}):"
        )
        lines += [f"  - {k.table}.{k.name}" for k in drift.missing_in_target]
    if drift.mismatched:
        lines.append(
            f"{len(drift.mismatched)} constraint(s) with a DIFFERENT definition in "
            f"target ({target}):"
        )
        for key, ref_def, tgt_def in drift.mismatched:
            lines.append(f"  - {key.table}.{key.name}")
            lines.append(f"      reference: {ref_def}")
            lines.append(f"      target:    {tgt_def}")
    if drift.target_only:
        lines.append(
            f"note: {len(drift.target_only)} constraint(s) present only in target "
            f"({target}) — not drift (stale reference or pending-removal leftover):"
        )
        lines += [f"  - {k.table}.{k.name}" for k in drift.target_only]
    return "\n".join(lines)
