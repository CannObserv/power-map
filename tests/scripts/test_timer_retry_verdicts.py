"""Each DB-backed timer script rides out a transient connect failure, then reports once (#833).

The real ``main()`` runs the real asyncpg client against a loopback port nothing
listens on, as #831's note recommends: no network, no database. Sleeping is
recorded, never waited. The assertion is on the verdict systemd would see (an
exception escaping ``python -m`` is exit 1), the attempts made and the delays
slept: the whole budget, once, then a failure in the same run.

The scripts are the ``in-process`` rows of ``tests/test_infra_retry.py``.
"""

import socket
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path

import asyncpg
import pytest

from src.core import retry
from src.core.retry import DELAYS
from tests.test_infra_retry import DB_MODULES


def _refused_dsn(dbname: str = "db") -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"postgresql://u:p@127.0.0.1:{port}/{dbname}"


def _plain(dsn: str, tmp: Path) -> list[str]:
    return ["--database-url", dsn]


# module -> argv builder. Every flag that would read a default target is given.
ARGV: dict[str, Callable[[str, Path], list[str]]] = {
    "scripts.check_api_anomalies": lambda dsn, tmp: ["--database-url", dsn, "--threshold", "100"],
    "scripts.audit_ancillary_orphans": _plain,
    "scripts.audit_assignment_relationship_windows": _plain,
    "scripts.prune": _plain,
    "scripts.audit_seat_occupancy": _plain,
    "scripts.audit_schema_constraint_parity": lambda dsn, tmp: [
        "--target-url",
        dsn,
        "--reference-url",
        dsn.rsplit("/", 1)[0] + "/reference",
        "--state-file",
        str(tmp / "state.json"),
    ],
    "scripts.export_pm_tables": lambda dsn, tmp: ["--database-url", dsn, "--root", str(tmp)],
    "scripts.apply_desired_state": lambda dsn, tmp: [
        "--database-url",
        dsn,
        "--desired",
        str(tmp / "desired"),
        "--out",
        str(tmp / "out"),
    ],
}


def verdict(main: Callable[[], int | None]) -> int:
    """The exit status ``python -m`` would give this ``main``."""
    try:
        code = main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception:
        return 1
    return code or 0


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(retry, "_sleep", record)
    return delays


@pytest.fixture
def connects(monkeypatch) -> list[str]:
    """Each DSN asyncpg.connect was asked for; the real client still runs."""
    calls: list[str] = []
    real = asyncpg.connect

    async def spy(dsn, *args, **kwargs):
        calls.append(dsn)
        return await real(dsn, *args, **kwargs)

    monkeypatch.setattr(asyncpg, "connect", spy)
    return calls


def test_the_table_covers_every_db_timer_script():
    """Kept in step with the policy test, so a new DB timer gets a verdict test too."""
    assert sorted(ARGV) == DB_MODULES


@pytest.mark.parametrize("module", sorted(ARGV))
def test_a_refused_database_spends_the_budget_then_fails_once(
    module, tmp_path, monkeypatch, slept, connects
):
    monkeypatch.setattr(sys, "argv", [module, *ARGV[module](_refused_dsn(), tmp_path)])
    assert verdict(import_module(module).main) == 1
    assert len(connects) == len(DELAYS) + 1
    assert slept == list(DELAYS)
