"""The operator entry point for the applier (#499 step 7).

Dry run by default under the house rules (#402/#399): the target is echoed,
`--execute` is a flag, and a dry run writes the artifact and a ledger line
and exits 0 for clean *and* blocked (seventeen pending creates must not fail
the timer every night), 3 for stale. `--execute` refuses (exit 1) without a
clean streak carrying this run's digest, applies in one verified transaction
when it has one, and records a rolled-back execute so the streak restarts.
"""

import itertools
import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

pytest.importorskip("duckdb")

from scripts import apply_desired_state as cli  # noqa: E402
from src.core.ingestion.applier import ApplierError  # noqa: E402
from src.core.ingestion.applier_report import LEDGER, read_ledger  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeConn, FakeLiveStore, write_desired  # noqa: E402

P1, P3 = "01P1", "01P3"
PM1 = "01M1"
MANIFEST = load_manifest()


def xw(producer_id, pm_id, resolution="live"):
    return {
        "source": PRODUCER_SOURCE,
        "kind": "person",
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": resolution,
    }


def _clock():
    start = datetime(2026, 9, 11, 9, 30, tzinfo=UTC)
    ticks = itertools.count()
    return lambda: start + timedelta(minutes=next(ticks))


@pytest.fixture
def world(tmp_path):
    desired = tmp_path / "desired"
    desired.mkdir()
    (desired / "BUILD.json").write_text(json.dumps({"datasets": {"persons": "v1"}}))
    return {"desired": desired, "out": tmp_path / "applier", "now": _clock()}


def _store(*, crosswalk=(), people=()):
    store = FakeLiveStore(crosswalk=crosswalk, tables={"people": list(people), "person_names": []})
    return lambda conn: store


async def _run(world, store, *, execute=False, conn=None, **kw):
    return await cli.run(
        conn or FakeConn(),
        desired=world["desired"],
        out=world["out"],
        execute=execute,
        store_factory=store,
        now=world["now"],
        **kw,
    )


def _summaries(world):
    return sorted(p for p in world["out"].glob("*/summary.json"))


# --- dry runs -------------------------------------------------------------------


async def test_a_clean_dry_run_writes_the_artifact_and_a_ledger_line_and_exits_zero(world):
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])

    code = await _run(world, store)

    assert code == 0
    summary = json.loads(_summaries(world)[0].read_text())
    assert summary["verdict"] == "clean" and summary["mode"] == "dry"
    assert summary["run_id"] == "2026-09-11T093000Z"
    assert summary["build_info"] == {"datasets": {"persons": "v1"}}
    ledger = read_ledger(world["out"] / LEDGER)
    assert [ln["mode"] for ln in ledger] == ["dry"] and ledger[0]["verdict"] == "clean"


async def test_a_blocked_dry_run_still_exits_zero(world):
    write_desired(world["desired"], desired_people=[{"pm_id": None, "producer_id": P3}])

    code = await _run(world, _store())

    assert code == 0
    assert json.loads(_summaries(world)[0].read_text())["verdict"] == "blocked"


async def test_a_stale_dry_run_exits_three(world):
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, "01M9", "merged")])

    code = await _run(world, store)

    assert code == 3
    assert json.loads(_summaries(world)[0].read_text())["verdict"] == "stale"


async def test_overrides_reach_the_thresholds(world):
    write_desired(world["desired"], desired_people=[{"pm_id": None, "producer_id": P3}])

    code = await _run(world, _store(), thresholds=cli.thresholds_with(allow_creates=1))

    assert code == 0
    assert json.loads(_summaries(world)[0].read_text())["verdict"] == "clean"


# --- execute ---------------------------------------------------------------------


async def test_execute_refuses_without_a_streak_and_opens_no_transaction(world):
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])
    conn = FakeConn()

    code = await _run(world, store, execute=True, conn=conn)

    assert code == 1
    assert conn.events == []
    assert [ln["mode"] for ln in read_ledger(world["out"] / LEDGER)] == ["refused"]


async def test_three_refused_attempts_do_not_build_the_streak_they_wait_on(world):
    """CR 3: a refusal was recorded as a clean dry run, so the operator's own attempts
    were the streak — three `--execute` invocations seconds apart wrote on the third.
    A refusal is its own mode now, and `may_execute` already refuses any line that is
    not a dry run, so only the nightly timer's dry runs build the gate."""
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])
    conns = [FakeConn() for _ in range(3)]

    codes = [await _run(world, store, execute=True, conn=c) for c in conns]

    assert codes == [1, 1, 1]
    assert all(c.events == [] for c in conns)
    assert [ln["mode"] for ln in read_ledger(world["out"] / LEDGER)] == ["refused"] * 3


async def test_execute_refuses_a_blocked_run_even_after_a_streak(world):
    write_desired(world["desired"], desired_people=[{"pm_id": None, "producer_id": P3}])
    store = _store()
    for _ in range(3):
        await _run(world, store, thresholds=cli.thresholds_with(allow_creates=1))
    conn = FakeConn()

    code = await _run(world, store, execute=True, conn=conn)

    assert code == 1 and conn.events == []


async def test_execute_applies_after_a_clean_streak_with_one_digest(world):
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])
    for _ in range(3):
        assert await _run(world, store) == 0
    conn = FakeConn()

    code = await _run(world, store, execute=True, conn=conn)

    assert code == 0
    assert conn.events == ["begin", "commit"]
    last = read_ledger(world["out"] / LEDGER)[-1]
    assert last["mode"] == "execute" and last["verdict"] == "clean"


async def test_a_shorter_streak_flag_is_honoured(world):
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])
    await _run(world, store)

    assert await _run(world, store, execute=True, streak=1) == 0


async def test_a_rolled_back_execute_is_recorded_and_breaks_the_streak(world):
    """The fake connection executes nothing, so a write that should have landed has not:
    the in-transaction re-diff still wants it, and the run rolls back."""
    write_desired(
        world["desired"],
        desired_people=[{"pm_id": PM1, "producer_id": P1}],
        desired_person_names=[
            {"pm_id": PM1, "producer_id": P1, "name": "New Name", "name_type": "legal"}
        ],
    )
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])
    for _ in range(3):
        assert await _run(world, store) == 0
    conn = FakeConn()

    code = await _run(world, store, execute=True, conn=conn)

    assert code == 1
    assert conn.events == ["begin", "rollback"]
    last = read_ledger(world["out"] / LEDGER)[-1]
    assert last["mode"] == "execute" and last["verdict"] == "rolled_back"
    ok, why = cli.may_execute(read_ledger(world["out"] / LEDGER), digest=last["digest"], streak=3)
    assert not ok and "execute" in why


async def test_a_database_error_during_execute_is_recorded_as_rolled_back(world, monkeypatch):
    """A trigger (the org-cycle guard) raises inside the transaction: nothing lands, the
    ledger says rolled_back, the exit code says refused — never a traceback."""
    write_desired(world["desired"], desired_people=[{"pm_id": PM1, "producer_id": P1}])
    store = _store(crosswalk=[xw(P1, PM1)], people=[{"id": PM1, "archived_at": None}])
    for _ in range(3):
        assert await _run(world, store) == 0

    async def boom(*a, **kw):
        raise asyncpg.PostgresError("org hierarchy cycle detected")

    monkeypatch.setattr(cli, "apply_diff", boom)

    code = await _run(world, store, execute=True)

    assert code == 1
    assert read_ledger(world["out"] / LEDGER)[-1]["verdict"] == "rolled_back"


# --- main ------------------------------------------------------------------------


def test_main_wires_the_flags_and_the_dsn(monkeypatch, tmp_path):
    seen = {}

    async def fake_run_against(dsn, **kw):
        seen.update(kw, dsn=dsn)
        return 0

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/pm")
    monkeypatch.setattr(cli, "_run_against", fake_run_against)

    code = cli.main(
        [
            "--desired",
            str(tmp_path / "d"),
            "--out",
            str(tmp_path / "o"),
            "--allow-creates",
            "4",
            "--max-updates",
            "50",
            "--allow-merges",
            "1",
            "--streak",
            "2",
            "--execute",
        ]
    )

    assert code == 0
    assert seen["dsn"] == "postgres://u:p@db.example/pm"
    assert seen["execute"] is True and seen["streak"] == 2
    assert seen["thresholds"].creates == 4 and seen["thresholds"].updates == 50
    assert seen["thresholds"].merges == 1
    assert seen["desired"] == tmp_path / "d" and seen["out"] == tmp_path / "o"


def test_main_defaults_to_a_dry_run_with_the_manifests_thresholds(monkeypatch):
    seen = {}

    async def fake_run_against(dsn, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/pm")
    monkeypatch.setattr(cli, "_run_against", fake_run_against)

    assert cli.main([]) == 0
    assert seen["execute"] is False
    assert seen["thresholds"] == MANIFEST.thresholds and seen["streak"] == MANIFEST.streak


def test_main_reports_an_applier_error_as_a_sentence_and_exits_three(monkeypatch, capsys):
    async def failing(dsn, **kw):
        raise ApplierError("desired state has no desired_people.parquet under /x")

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/pm")
    monkeypatch.setattr(cli, "_run_against", failing)

    code = cli.main(["--desired", "/x"])

    out = capsys.readouterr().out
    assert code == 3
    assert "desired_people" in out and "Traceback" not in out


def test_the_applier_scopes_by_the_source_the_seed_writes():
    """The first production dry run matched zero crosswalk rows: the seed wrote `usa_wa`,
    the applier asked for `usa-wa`. One constant now, and this pins it."""
    from scripts import seed_producer_crosswalk

    assert cli.SOURCE == PRODUCER_SOURCE == "usa_wa"
    assert seed_producer_crosswalk.DEFAULT_SOURCE == PRODUCER_SOURCE


@pytest.mark.parametrize(
    "flag",
    [
        ["--streak", "0"],
        ["--streak", "-1"],
        ["--allow-creates", "-1"],
        ["--max-updates", "-1"],
        ["--allow-merges", "-1"],
    ],
    ids=[
        "zero streak",
        "negative streak",
        "negative creates",
        "negative updates",
        "negative merges",
    ],
)
def test_a_count_flag_that_could_only_weaken_the_gate_is_a_usage_error(flag, monkeypatch):
    """CR 2: `--streak 0` opened the gate on an empty ledger — `[-0:]` is the whole
    ledger and `len(recent) < 0` is never true — and a negative threshold blocks a
    run with nothing to block. Neither is a thing an operator can mean."""

    async def never(dsn, **kw):
        raise AssertionError("connected despite a bad flag")

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/pm")
    monkeypatch.setattr(cli, "_run_against", never)

    with pytest.raises(SystemExit) as exc:
        cli.main([*flag, "--execute"])

    assert exc.value.code == 2


def test_a_bad_count_flag_says_what_it_wanted(monkeypatch, capsys):
    """CR 16: argparse builds its message from `type.__name__`, and the closure was
    called `parse` — "invalid parse value: 'abc'" told the operator nothing."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/pm")

    with pytest.raises(SystemExit):
        cli.main(["--streak", "abc"])

    assert "invalid count value: 'abc'" in capsys.readouterr().err
