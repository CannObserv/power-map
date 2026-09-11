"""Verdicts, the run artifact, the ledger and the execute gate (#499 step 5).

A dry run always completes and records a verdict:

    clean    nothing exceeded a threshold
    blocked  a threshold was exceeded (creates, merges, conflicts, updates)
    stale    the desired state disagrees with the live crosswalk — rebuild

The artifact under ``data/applier/<run-id>/`` is what #501's triage works
from: ``diff.jsonl`` holds one actionable entry per line (never a noop),
sorted by ``entry_id`` so a decision can name a line and a diff between two
runs is a diff of two files; ``summary.json`` carries the verdict, counts,
thresholds, the diff's digest and the inputs (`BUILD.json`); ``summary.md``
says the same for a person. ``ledger.jsonl`` gets one line per run, carrying
the mode it ran in — ``dry``, ``execute``, or ``refused`` for an ``--execute``
the gate turned away. Only a dry run builds the streak.

The digest covers the actionable entries only — id, kind, target and the
value changes, never the prose — in a fixed order, so two runs that would do
the same thing agree whatever order the engine emitted them in.
``may_execute`` is the gate: ``streak`` consecutive clean dry runs carrying one
digest, which the current run must reproduce.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.core.ingestion.applier import ENTRY_KINDS, Diff, Entry
from src.core.ingestion.mapping.manifest import Thresholds

__all__ = [
    "DIFF_FILE",
    "LEDGER",
    "MODES",
    "SUMMARY_JSON",
    "SUMMARY_MD",
    "THRESHOLD_KINDS",
    "VERDICTS",
    "Verdict",
    "append_ledger",
    "diff_digest",
    "entry_json",
    "ledger_line",
    "may_execute",
    "read_ledger",
    "run_id_for",
    "verdict_for",
    "write_report",
]

LEDGER = "ledger.jsonl"
DIFF_FILE = "diff.jsonl"
SUMMARY_JSON = "summary.json"
SUMMARY_MD = "summary.md"
# `rolled_back` is recorded only by an execute whose in-transaction re-diff
# still had writes; it breaks the streak so the next execute re-earns it.
VERDICTS = ("clean", "blocked", "stale", "rolled_back")

# How a run reached the ledger. `dry` is the only mode that builds the streak,
# which is why a refused `--execute` is `refused` and not a dry run (CR 3): the
# operator's own attempts used to be the streak they were waiting on.
MODES = ("dry", "execute", "refused")

# threshold name → the entry kinds it counts. `updates` is every write to a
# row PM already has or a child row it lacks; `creates` is new entities only.
THRESHOLD_KINDS: dict[str, tuple[str, ...]] = {
    "creates": ("create",),
    "merges": ("merge",),
    "conflicts": ("conflict",),
    "stale": ("stale",),
    "updates": ("update", "insert"),
}


@dataclass(frozen=True)
class Verdict:
    """The run's verdict and, per exceeded threshold, (count, limit)."""

    verdict: str
    exceeded: dict[str, tuple[int, int | None]]


def verdict_for(diff: Diff, thresholds: Thresholds) -> Verdict:
    counts = diff.counts
    exceeded: dict[str, tuple[int, int | None]] = {}
    for name, kinds in THRESHOLD_KINDS.items():
        limit = getattr(thresholds, name)
        n = sum(counts[k] for k in kinds)
        if limit is not None and n > limit:
            exceeded[name] = (n, limit)
    if "stale" in exceeded:
        verdict = "stale"
    elif exceeded:
        verdict = "blocked"
    else:
        verdict = "clean"
    return Verdict(verdict, exceeded)


def _actionable(diff: Diff) -> list[Entry]:
    return sorted((e for e in diff.entries if e.kind != "noop"), key=lambda e: (e.entry_id, e.kind))


def _changes(entry: Entry) -> dict[str, list]:
    return {col: [old, new] for col, (old, new) in entry.changes.items()}


def _digest_view(entry: Entry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "table": entry.table,
        "kind": entry.kind,
        "producer_id": entry.producer_id,
        "pm_id": entry.pm_id,
        "row_id": entry.row_id,
        "changes": _changes(entry),
    }


def diff_digest(diff: Diff) -> str:
    """sha256 over the actionable entries in a fixed order; prose and hints excluded."""
    body = json.dumps([_digest_view(e) for e in _actionable(diff)], sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def entry_json(entry: Entry) -> dict:
    """One diff.jsonl line."""
    return {**_digest_view(entry), "reason": entry.reason, "hint": list(entry.hint)}


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run_id_for(moment: datetime) -> str:
    """UTC to the second: sorts, and is a valid directory name everywhere."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")


def _by_table(diff: Diff) -> dict[str, dict[str, int]]:
    tables: dict[str, dict[str, int]] = {}
    for e in diff.entries:
        tables.setdefault(e.table, {k: 0 for k in ENTRY_KINDS})[e.kind] += 1
    return dict(sorted(tables.items()))


def _markdown(summary: dict) -> str:
    lines = [
        f"# Applier run {summary['run_id']} — {summary['mode']} — verdict: {summary['verdict']}",
        "",
        f"Source `{summary['source']}`; digest `{summary['digest'][:12]}…`; "
        f"{summary['entries']} actionable entries in `diff.jsonl`.",
    ]
    datasets = (summary.get("build_info") or {}).get("datasets") or {}
    if datasets:
        lines.append("Built from " + ", ".join(f"{k}@{v}" for k, v in datasets.items()) + ".")
    if summary["exceeded"]:
        over = ", ".join(f"{k} {n} > {limit}" for k, (n, limit) in summary["exceeded"].items())
        lines += ["", f"**Thresholds exceeded:** {over}"]
    lines += [
        "",
        "| table | " + " | ".join(ENTRY_KINDS) + " |",
        "|---|" + "---|" * len(ENTRY_KINDS),
    ]
    for table, counts in summary["by_table"].items():
        lines.append(f"| {table} | " + " | ".join(str(counts[k]) for k in ENTRY_KINDS) + " |")
    lines.append("")
    return "\n".join(lines)


def write_report(
    run_dir: Path | str,
    *,
    run_id: str,
    mode: str,
    diff: Diff,
    verdict: Verdict,
    thresholds: Thresholds,
    build_info: dict | None,
    source: str,
    started_at: datetime,
    finished_at: datetime,
) -> dict:
    """Write diff.jsonl, summary.json and summary.md; return the summary (JSON-native)."""
    if mode not in MODES:
        raise ValueError(f"unknown run mode {mode!r} (one of {', '.join(MODES)})")
    out = Path(run_dir)
    out.mkdir(parents=True, exist_ok=True)
    actionable = _actionable(diff)
    with (out / DIFF_FILE).open("w") as f:
        for e in actionable:
            f.write(json.dumps(entry_json(e), sort_keys=True, default=str) + "\n")
    summary = {
        "run_id": run_id,
        "mode": mode,
        "source": source,
        "verdict": verdict.verdict,
        "exceeded": {k: [n, limit] for k, (n, limit) in verdict.exceeded.items()},
        "counts": diff.counts,
        "by_table": _by_table(diff),
        "entries": len(actionable),
        "digest": diff_digest(diff),
        "thresholds": asdict(thresholds),
        "build_info": build_info,
        "started_at": _ts(started_at),
        "finished_at": _ts(finished_at),
    }
    (out / SUMMARY_JSON).write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    (out / SUMMARY_MD).write_text(_markdown(summary))
    return summary


def ledger_line(summary: dict) -> dict:
    """The one line a run appends to the ledger."""
    return {
        "run_id": summary["run_id"],
        "started_at": summary["started_at"],
        "mode": summary["mode"],
        "verdict": summary["verdict"],
        "digest": summary["digest"],
        "counts": summary["counts"],
        "exceeded": summary["exceeded"],
        "datasets": (summary.get("build_info") or {}).get("datasets"),
    }


def append_ledger(path: Path | str, line: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(line, sort_keys=True, default=str) + "\n")


def read_ledger(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def may_execute(ledger: Sequence[dict], *, digest: str, streak: int) -> tuple[bool, str]:
    """The gate: the last ``streak`` runs are clean dry runs carrying ``digest``.

    A streak below 1 is refused here rather than honoured by the slice (CR 2):
    ``[-0:]`` is the whole ledger, not its last zero lines, and ``len(recent)
    < 0`` is never true — so a zero read an empty ledger as a satisfied one and
    answered yes. The CLI refuses the flag too; this is the gate refusing to be
    asked at all, whoever is asking.

    The lines present are judged before they are counted (CR 12), so the count
    is only ever reported when every one of them qualifies — a refused attempt
    is named as the blocker, never tallied as progress towards the streak.
    """
    if streak < 1:
        return False, f"a streak of {streak} is no gate; at least one clean dry run is required"
    recent = list(ledger)[-streak:]
    for ln in recent:
        if ln.get("mode") != "dry":
            return False, f"run {ln.get('run_id')} was an execute; the streak restarts after it"
        if ln.get("verdict") != "clean":
            return False, f"run {ln.get('run_id')} was {ln.get('verdict')}"
        if ln.get("digest") != digest:
            return False, f"the diff changed since run {ln.get('run_id')}"
    if len(recent) < streak:
        return False, f"only {len(recent)} of {streak} dry runs recorded"
    return True, f"{streak} consecutive clean dry runs with this digest"
