"""``scripts/prune_outbox.py``: one retention window per table, no hold (#502).

The single ``--retention-days`` flag aligned three tables with different jobs
on one window, and carried the #495 104-day hold. Each table now has its own
flag, defaulting to the module's per-table window. Pure unit: parses arguments
and reads the unit file; no DB.
"""

from pathlib import Path

import pytest

from scripts import prune_outbox
from src.core.maintenance import DEFAULT_RETENTION, Retention

UNIT = Path(__file__).resolve().parents[2] / "infra" / "power-map-prune.service"


def _parse(*argv: str):
    return prune_outbox.build_arg_parser().parse_args(list(argv))


def test_no_flags_use_the_per_table_defaults():
    assert prune_outbox.retention_from_args(_parse()) == DEFAULT_RETENTION


def test_each_flag_sets_only_its_own_table():
    args = _parse(
        "--changes-retention-days",
        "120",
        "--tombstone-retention-days",
        "30",
        "--request-log-retention-days",
        "60",
    )
    assert prune_outbox.retention_from_args(args) == Retention(
        entity_changes=120, deleted_entities=30, api_request_log=60
    )


def test_the_single_window_flag_is_gone(capsys):
    with pytest.raises(SystemExit):
        _parse("--retention-days", "104")
    assert "--retention-days" in capsys.readouterr().err


def test_a_bad_window_names_the_flag_the_operator_typed(monkeypatch, capsys):
    """The usage error names ``--request-log-retention-days``, not the field behind it."""
    monkeypatch.setattr("sys.argv", ["prune_outbox", "--request-log-retention-days", "0"])
    with pytest.raises(SystemExit):
        prune_outbox.main()
    err = capsys.readouterr().err
    assert "--request-log-retention-days" in err
    assert "api_request_log" not in err


def test_installed_unit_runs_on_the_defaults():
    """The #495 hold is released: the unit names no window, so the defaults govern."""
    exec_start = [line for line in UNIT.read_text().splitlines() if line.startswith("ExecStart=")]
    assert exec_start == ["ExecStart=uv run python -m scripts.prune_outbox --execute"]
