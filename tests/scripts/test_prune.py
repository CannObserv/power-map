"""``scripts/prune.py``: one retention window per table, no hold (#502, #651).

The single ``--retention-days`` flag aligned three tables with different jobs
on one window, and carried the #495 104-day hold. Each table now has its own
flag, defaulting to the module's per-table window. The outbox's flag went with
the outbox (#651), and the script lost its ``_outbox`` suffix while the unit kept
its name. Pure unit: parses arguments, reads the unit file, and drives ``run``
against stand-ins; no DB.
"""

import logging
from pathlib import Path

import pytest

from scripts import prune
from src.core.maintenance import DEFAULT_RETENTION, PruneResult, Retention

UNIT = Path(__file__).resolve().parents[2] / "infra" / "power-map-prune.service"


def _parse(*argv: str):
    return prune.build_arg_parser().parse_args(list(argv))


def test_no_flags_use_the_per_table_defaults():
    assert prune.retention_from_args(_parse()) == DEFAULT_RETENTION


def test_each_flag_sets_only_its_own_table():
    args = _parse("--tombstone-retention-days", "30", "--request-log-retention-days", "60")
    assert prune.retention_from_args(args) == Retention(deleted_entities=30, api_request_log=60)


@pytest.mark.parametrize("flag", ["--retention-days", "--changes-retention-days"])
def test_retired_window_flags_are_gone(capsys, flag):
    with pytest.raises(SystemExit):
        _parse(flag, "104")
    assert flag in capsys.readouterr().err


def test_a_bad_window_names_the_flag_the_operator_typed(monkeypatch, capsys):
    """The usage error names ``--request-log-retention-days``, not the field behind it."""
    monkeypatch.setattr("sys.argv", ["prune", "--request-log-retention-days", "0"])
    with pytest.raises(SystemExit):
        prune.main()
    err = capsys.readouterr().err
    assert "--request-log-retention-days" in err
    assert "api_request_log" not in err


def test_installed_unit_runs_on_the_defaults():
    """The #495 hold is released: the unit names no window, so the defaults govern."""
    exec_start = [line for line in UNIT.read_text().splitlines() if line.startswith("ExecStart=")]
    assert exec_start == ["ExecStart=uv run python -m scripts.prune --execute"]


class _Conn:
    """Stands in for the asyncpg connection ``run`` opens; only ``close`` is called."""

    closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("execute", "expected"),
    [
        (
            False,
            "Dry run — 3 deleted_entities (>30d) + 4 api_request_log (>60d) row(s) are eligible",
        ),
        (True, "Pruned 3 deleted_entities (>30d) + 4 api_request_log (>60d) row(s)"),
    ],
)
async def test_run_reports_each_table_against_its_own_window(
    monkeypatch, caplog, execute, expected
):
    """Both log lines pair each count with its own table's window.

    The ``%d`` placeholders and their arguments are matched by hand, and a
    mismatch never raises from ``logger.info`` — logging prints a traceback and
    the unit still exits 0 — so the operator's line would simply go missing.
    ``getMessage()`` here is what renders it, and raises on a mismatch.
    """
    conn = _Conn()
    result = PruneResult(deleted_entities=3, api_request_log=4)

    async def _connect(dsn):
        return conn

    async def _counted(c, retention):
        assert c is conn
        return result

    monkeypatch.setattr(prune.asyncpg, "connect", _connect)
    monkeypatch.setattr(prune, "count_prunable", _counted)
    monkeypatch.setattr(prune, "prune_expired", _counted)
    caplog.set_level(logging.INFO, logger=prune.logger.name)

    await prune.run(
        "postgresql://unused",
        execute=execute,
        retention=Retention(deleted_entities=30, api_request_log=60),
    )

    messages = [r.getMessage() for r in caplog.records if r.name == prune.logger.name]
    assert len(messages) == 1 and messages[0].startswith(expected), messages
    assert conn.closed
