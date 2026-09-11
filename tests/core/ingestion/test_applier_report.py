"""Thresholds, verdicts, the artifact, the ledger and the execute gate (#499 step 5).

A dry run always completes and records a verdict — `clean`, `blocked` (a
threshold exceeded) or `stale` (the desired state must be rebuilt). The diff's
digest covers the actionable entries only, in a fixed order, so two runs that
would do the same thing agree whatever order the engine emitted them in.
`--execute` is offered only after `streak` consecutive clean dry runs carrying
one digest, which the current run must reproduce.
"""

import json
from datetime import UTC, datetime

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import Diff, Entry  # noqa: E402
from src.core.ingestion.applier_report import (  # noqa: E402
    LEDGER,
    append_ledger,
    diff_digest,
    may_execute,
    read_ledger,
    run_id_for,
    verdict_for,
    write_report,
)
from src.core.ingestion.mapping.manifest import Thresholds  # noqa: E402

NOW = datetime(2026, 9, 11, 9, 30, 5, tzinfo=UTC)


def E(kind, key="01P1", table="desired_people", **kw) -> Entry:
    pm_id = kw.pop("pm_id", None)
    return Entry(
        entry_id=f"{table}:{key}", table=table, kind=kind, producer_id=key, pm_id=pm_id, **kw
    )


# --- verdicts -------------------------------------------------------------------


def test_a_diff_within_thresholds_is_clean():
    diff = Diff([E("noop"), E("update", "01P2", changes={"name": ("a", "b")})])

    verdict = verdict_for(diff, Thresholds())

    assert verdict.verdict == "clean" and verdict.exceeded == {}


def test_a_create_exceeds_the_default_threshold():
    verdict = verdict_for(Diff([E("create")]), Thresholds())

    assert verdict.verdict == "blocked"
    assert verdict.exceeded == {"creates": (1, 0)}


def test_stale_outranks_blocked():
    verdict = verdict_for(Diff([E("stale"), E("create", "01P2")]), Thresholds())

    assert verdict.verdict == "stale"
    assert "stale" in verdict.exceeded and "creates" in verdict.exceeded


def test_updates_threshold_counts_updates_and_inserts():
    diff = Diff([E("update", changes={"a": (1, 2)}), E("insert", "01P2", changes={"a": (None, 1)})])

    assert verdict_for(diff, Thresholds(updates=1)).exceeded == {"updates": (2, 1)}
    assert verdict_for(diff, Thresholds(updates=2)).verdict == "clean"


def test_unlimited_updates_never_block():
    diff = Diff([E("update", f"01P{n}", changes={"a": (1, 2)}) for n in range(500)])

    assert verdict_for(diff, Thresholds()).verdict == "clean"


def test_merges_and_conflicts_have_their_own_thresholds():
    diff = Diff([E("merge", table="desired_person_merges"), E("conflict", "01P2")])

    assert verdict_for(diff, Thresholds()).exceeded == {"merges": (1, 0), "conflicts": (1, 0)}


# --- digest ---------------------------------------------------------------------


def test_the_digest_ignores_entry_order_and_noops():
    a = Diff(
        [E("noop", "01P9"), E("update", "01P1", changes={"n": ("x", "y")}), E("create", "01P2")]
    )
    b = Diff(
        [E("create", "01P2"), E("update", "01P1", changes={"n": ("x", "y")}), E("noop", "01P8")]
    )

    assert diff_digest(a) == diff_digest(b)
    assert len(diff_digest(a)) == 64


def test_the_digest_changes_when_an_entry_changes():
    a = Diff([E("update", changes={"n": ("x", "y")})])
    b = Diff([E("update", changes={"n": ("x", "z")})])
    c = Diff([E("update", changes={"n": ("x", "y")}, reason="a different reason text")])

    assert diff_digest(a) != diff_digest(b)
    assert diff_digest(a) == diff_digest(c)  # prose is not the diff


# --- the artifact ---------------------------------------------------------------


def _report(tmp_path, diff, *, mode="dry", verdict=None):
    verdict = verdict or verdict_for(diff, Thresholds())
    return write_report(
        tmp_path / "run",
        run_id="2026-09-11T093005Z",
        mode=mode,
        diff=diff,
        verdict=verdict,
        thresholds=Thresholds(),
        build_info={"datasets": {"persons": "v1"}},
        source="usa-wa",
        started_at=NOW,
        finished_at=NOW,
    )


def test_the_report_writes_actionable_entries_sorted_and_counts_noops(tmp_path):
    diff = Diff(
        [
            E("noop", "01P3"),
            E("update", "01P2", pm_id="01M2", changes={"name": ("old", "new")}, row_id="n2"),
            E("create", "01P1", hint=({"table": "person_names", "parent": "01MX"},)),
        ]
    )

    summary = _report(tmp_path, diff)

    lines = [json.loads(ln) for ln in (tmp_path / "run" / "diff.jsonl").read_text().splitlines()]
    assert [ln["entry_id"] for ln in lines] == ["desired_people:01P1", "desired_people:01P2"]
    assert lines[1] == {
        "entry_id": "desired_people:01P2",
        "table": "desired_people",
        "kind": "update",
        "producer_id": "01P2",
        "pm_id": "01M2",
        "row_id": "n2",
        "changes": {"name": ["old", "new"]},
        "reason": None,
        "hint": [],
    }
    assert lines[0]["hint"] == [{"table": "person_names", "parent": "01MX"}]
    assert summary["counts"]["noop"] == 1 and summary["counts"]["create"] == 1
    assert json.loads((tmp_path / "run" / "summary.json").read_text()) == summary


def test_the_summary_carries_verdict_digest_inputs_and_timestamps(tmp_path):
    diff = Diff([E("create")])

    summary = _report(tmp_path, diff)

    assert summary["run_id"] == "2026-09-11T093005Z" and summary["mode"] == "dry"
    assert summary["verdict"] == "blocked" and summary["exceeded"] == {"creates": [1, 0]}
    assert summary["digest"] == diff_digest(diff)
    assert summary["thresholds"] == {
        "creates": 0,
        "merges": 0,
        "conflicts": 0,
        "stale": 0,
        "updates": None,
    }
    assert summary["build_info"] == {"datasets": {"persons": "v1"}}
    assert summary["started_at"] == "2026-09-11T09:30:05.000000Z"
    assert summary["source"] == "usa-wa"


def test_the_markdown_summary_is_readable(tmp_path):
    diff = Diff([E("create"), E("update", "01P2", changes={"name": ("a", "b")})])

    _report(tmp_path, diff)

    md = (tmp_path / "run" / "summary.md").read_text()
    assert "blocked" in md and "creates" in md
    assert "desired_people" in md and "| create" in md or "create |" in md


# --- the ledger and the gate -----------------------------------------------------


def _line(run_id, *, verdict="clean", digest="d1", mode="dry"):
    return {"run_id": run_id, "mode": mode, "verdict": verdict, "digest": digest}


def test_the_ledger_appends_one_line_per_run(tmp_path):
    path = tmp_path / LEDGER

    append_ledger(path, _line("r1"))
    append_ledger(path, _line("r2", verdict="blocked"))

    assert [ln["run_id"] for ln in read_ledger(path)] == ["r1", "r2"]
    assert read_ledger(tmp_path / "absent.jsonl") == []


def test_execute_is_offered_after_a_clean_streak_with_one_digest():
    ledger = [_line("r1"), _line("r2"), _line("r3")]

    ok, reason = may_execute(ledger, digest="d1", streak=3)

    assert ok and "3" in reason


def test_a_short_streak_refuses():
    ok, reason = may_execute([_line("r1"), _line("r2")], digest="d1", streak=3)

    assert not ok and "2 of 3" in reason


def test_a_blocked_night_in_the_streak_refuses():
    ledger = [_line("r1"), _line("r2", verdict="blocked"), _line("r3")]

    ok, reason = may_execute(ledger, digest="d1", streak=3)

    assert not ok and "r2" in reason and "blocked" in reason


def test_a_changed_digest_refuses():
    ledger = [_line("r1", digest="d0"), _line("r2"), _line("r3")]

    ok, reason = may_execute(ledger, digest="d1", streak=3)

    assert not ok and "r1" in reason and "changed" in reason


def test_the_current_run_must_reproduce_the_streaks_digest():
    ledger = [_line("r1"), _line("r2"), _line("r3")]

    ok, reason = may_execute(ledger, digest="d9", streak=3)

    assert not ok and "changed" in reason


def test_an_execute_in_the_streak_refuses():
    ledger = [_line("r1"), _line("r2", mode="execute"), _line("r3")]

    ok, reason = may_execute(ledger, digest="d1", streak=3)

    assert not ok and "r2" in reason and "execute" in reason


def test_run_ids_are_utc_to_the_second():
    assert run_id_for(NOW) == "2026-09-11T093005Z"


def test_a_non_positive_streak_never_opens_the_gate():
    """CR 2: `list(ledger)[-0:]` is the whole ledger, not its last zero lines, and
    `len(recent) < 0` is never true — so a zero streak read an empty ledger as a
    satisfied one and answered yes. The gate refuses to be asked that way."""
    clean = [_line("r1"), _line("r2"), _line("r3")]
    for streak in (0, -1):
        for ledger in ([], clean):
            ok, reason = may_execute(ledger, digest="d1", streak=streak)

            assert not ok and "streak" in reason


def test_a_short_ledger_names_the_line_that_blocks_it_before_counting():
    """CR 12: the length was checked first and counted lines of any mode, so a ledger
    ending in a refused attempt read "only 2 of 3 dry runs recorded" — progress, to
    the operator whose attempt had just reset the streak. The lines present are
    checked first; the count is only reported once every one of them qualifies."""
    ok, reason = may_execute([_line("r1"), _line("r2", mode="refused")], digest="d1", streak=3)

    assert not ok and "r2" in reason and "recorded" not in reason


def test_a_line_that_is_not_a_dry_run_is_named_by_its_own_mode():
    """CR 13: every non-dry line was reported as "an execute", so a refused attempt —
    which wrote nothing — read to the operator as a write."""
    ledger = [_line("r1"), _line("r2", mode="refused"), _line("r3")]

    ok, reason = may_execute(ledger, digest="d1", streak=3)

    assert not ok and "r2 was refused" in reason and "an execute" not in reason
