"""Tests for scripts._journal — journal priority for the stdlib scripts (#690, #692).

journald files a plain stderr line at ``info``. When stderr is the journal
(``$JOURNAL_STREAM`` names it), each line carries its syslog ``<N>`` prefix,
which systemd's default ``SyslogLevelPrefix=yes`` turns into the entry's
priority; off the journal the lines read plain.

The module is the one copy for the scripts: they run as ``/usr/bin/python3
<file>``, where their own directory is ``sys.path[0]``, so they import it as a
sibling. The rule itself lives in ``src/core/journal.py`` (#704), which the
module loads by path; the guard below holds it to that, and the last two tests
hold every script to the module.
"""

import ast
import inspect
import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import _journal
from scripts._journal import log_handler

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
CORE_JOURNAL = Path(__file__).resolve().parents[2] / "src" / "core" / "journal.py"
USERS = ("check_disk_headroom.py", "prune_disk.py", "status_checkin.py")


def _journal_env(stream) -> dict[str, str]:
    st = os.fstat(stream.fileno())
    return {"JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino}"}


@pytest.mark.parametrize(
    ("level", "prefix"),
    [
        (logging.CRITICAL, "<2>"),
        (logging.ERROR, "<3>"),
        (logging.WARNING, "<4>"),
        (logging.INFO, "<6>"),
        (logging.DEBUG, "<7>"),
    ],
)
def test_on_the_journal_each_line_carries_its_syslog_priority(level, prefix, tmp_path: Path):
    with open(tmp_path / "stderr", "w") as stream:
        handler = log_handler(stream, _journal_env(stream))
        record = logging.LogRecord("x", level, __file__, 1, "low headroom: %s", ("/",), None)
        assert handler.format(record).startswith(prefix + logging.getLevelName(level))


def test_a_level_between_the_named_ones_reads_as_info(tmp_path: Path):
    with open(tmp_path / "stderr", "w") as stream:
        handler = log_handler(stream, _journal_env(stream))
        record = logging.LogRecord("x", 25, __file__, 1, "boom", (), None)
        assert handler.format(record).startswith("<6>")


def test_off_the_journal_lines_carry_no_prefix(tmp_path: Path):
    """A terminal, or a stream that is not the one systemd named, reads plain."""
    with open(tmp_path / "stderr", "w") as stream, open(tmp_path / "other", "w") as other:
        for env in ({}, _journal_env(other), {"JOURNAL_STREAM": "junk"}):
            handler = log_handler(stream, env)
            record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
            assert handler.format(record) == "ERROR x: boom", env


def test_a_stream_without_a_descriptor_carries_no_prefix():
    handler = log_handler(io.StringIO(), {"JOURNAL_STREAM": "1:2"})
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
    assert handler.format(record) == "ERROR x: boom"


def test_the_rule_comes_from_src_core_journal(tmp_path: Path):
    """One rule for both homes (#704): the handler is the src module's class,
    and the helper keeps no priority map or ``$JOURNAL_STREAM`` check of its own."""
    with open(tmp_path / "stderr", "w") as stream:
        handler = log_handler(stream, {})
    assert type(handler).__name__ == "JournalStreamHandler"
    assert Path(inspect.getfile(type(handler))) == CORE_JOURNAL
    names = _names(ast.parse((SCRIPTS_DIR / "_journal.py").read_text()))
    assert "JOURNAL_STREAM" not in names
    assert "_SYSLOG_PRIORITY" not in names


def test_a_failed_rule_load_leaves_no_half_built_module(tmp_path: Path, monkeypatch):
    """importlib's own contract: a module whose execution raised is not left in
    ``sys.modules``, or the next load would return it without its handler."""
    monkeypatch.delitem(sys.modules, _journal.RULE_MODULE)
    with pytest.raises(FileNotFoundError):
        _journal._load_rule(tmp_path / "absent.py")
    assert _journal.RULE_MODULE not in sys.modules


def _names(tree: ast.Module) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.Name | ast.FunctionDef | ast.ClassDef):
            found.add(getattr(node, "id", None) or node.name)
    return found


@pytest.mark.parametrize("name", USERS)
def test_each_script_logs_through_the_one_helper(name):
    """No script keeps its own copy (the #690 pair did, held by a parity test)."""
    tree = ast.parse((SCRIPTS_DIR / name).read_text())
    names = _names(tree)
    assert "JOURNAL_STREAM" not in names, f"{name} reads $JOURNAL_STREAM itself"
    assert "_SYSLOG_PRIORITY" not in names, f"{name} keeps its own priority map"
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "log_handler"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "_journal"
    ]
    assert calls, f"{name} never calls _journal.log_handler"


# Each script's cheapest clean run, and a line only that run prints. The hook
# has no --help: with no unit it logs its usage line and exits 0.
FILE_RUNS = {
    "check_disk_headroom.py": (["--help"], "Usage:"),
    "prune_disk.py": (["--help"], "Usage:"),
    "status_checkin.py": ([], "usage: status_checkin.py <unit>"),
}


@pytest.mark.parametrize("name", USERS)
def test_each_script_imports_it_when_run_as_a_file(name):
    """The units' form: ``/usr/bin/python3 <file>``, no venv, cwd elsewhere.
    The script's directory is ``sys.path[0]``, so the sibling import resolves;
    the hook would survive without it, so its fallback warning must be absent."""
    argv, expected = FILE_RUNS[name]
    python = shutil.which("python3", path="/usr/bin") or sys.executable
    proc = subprocess.run(
        [python, str(SCRIPTS_DIR / name), *argv],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        cwd="/",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert expected in proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "helper unavailable" not in proc.stderr, proc.stderr
