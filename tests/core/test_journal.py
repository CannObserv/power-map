"""Tests for src.core.journal — the one journal-priority rule (#690, #692, #704).

journald files a plain stdout/stderr line at ``info``. When a handler's own
stream is the journal (``$JOURNAL_STREAM`` names its ``st_dev:st_ino``), each
line starts with its syslog ``<N>``, which systemd's default
``SyslogLevelPrefix=yes`` turns into the entry's priority and strips, so a JSON
line stays JSON. Off the journal the lines are unchanged.

The venv-less stdlib scripts load this file by path (``scripts/_journal.py``),
so it must stay standard-library only; the last test holds it to that.
"""

import ast
import io
import logging
import os
import sys
from pathlib import Path

import pytest

from src.core.journal import JournalStreamHandler

JOURNAL_PY = Path(__file__).resolve().parents[2] / "src" / "core" / "journal.py"


def _journal_env(stream) -> dict[str, str]:
    st = os.fstat(stream.fileno())
    return {"JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino}"}


def _record(level: int, msg: str = "boom") -> logging.LogRecord:
    return logging.LogRecord("x", level, __file__, 1, msg, (), None)


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
    with open(tmp_path / "out", "w") as stream:
        handler = JournalStreamHandler(stream, _journal_env(stream))
        assert handler.format(_record(level)) == prefix + "boom"


def test_a_level_between_the_named_ones_reads_as_info(tmp_path: Path):
    with open(tmp_path / "out", "w") as stream:
        handler = JournalStreamHandler(stream, _journal_env(stream))
        assert handler.format(_record(25)) == "<6>boom"


def test_the_prefix_reaches_the_stream(tmp_path: Path):
    """emit() writes what format() returns: one prefixed line per record."""
    with open(tmp_path / "out", "w") as stream:
        handler = JournalStreamHandler(stream, _journal_env(stream))
        handler.emit(_record(logging.WARNING))
    assert (tmp_path / "out").read_text() == "<4>boom\n"


def test_off_the_journal_lines_are_unchanged(tmp_path: Path):
    """No ``$JOURNAL_STREAM``, another stream's, or junk: the line is untouched."""
    with open(tmp_path / "out", "w") as stream, open(tmp_path / "other", "w") as other:
        for env in ({}, _journal_env(other), {"JOURNAL_STREAM": "junk"}):
            handler = JournalStreamHandler(stream, env)
            assert handler.format(_record(logging.ERROR)) == "boom", env


def test_only_the_stream_systemd_named_gets_the_prefix(tmp_path: Path):
    """``$JOURNAL_STREAM`` names one stream. A unit whose stderr is the journal
    and whose stdout is not must leave its stdout lines unprefixed (#704)."""
    with open(tmp_path / "stdout", "w") as stdout, open(tmp_path / "stderr", "w") as stderr:
        env = _journal_env(stderr)
        assert JournalStreamHandler(stdout, env).format(_record(logging.ERROR)) == "boom"
        assert JournalStreamHandler(stderr, env).format(_record(logging.ERROR)) == "<3>boom"


def test_a_stream_without_a_descriptor_is_unchanged():
    handler = JournalStreamHandler(io.StringIO(), {"JOURNAL_STREAM": "1:2"})
    assert handler.format(_record(logging.ERROR)) == "boom"


def test_the_environment_defaults_to_the_process_one(tmp_path: Path, monkeypatch):
    """dictConfig builds the handler with ``stream`` alone, so the default
    environment is the process's own."""
    with open(tmp_path / "out", "w") as stream:
        monkeypatch.setenv("JOURNAL_STREAM", _journal_env(stream)["JOURNAL_STREAM"])
        assert JournalStreamHandler(stream).format(_record(logging.ERROR)) == "<3>boom"
        monkeypatch.delenv("JOURNAL_STREAM")
        assert JournalStreamHandler(stream).format(_record(logging.ERROR)) == "boom"


def test_set_stream_rechecks_the_journal(tmp_path: Path):
    with open(tmp_path / "out", "w") as stream, open(tmp_path / "other", "w") as other:
        handler = JournalStreamHandler(stream, _journal_env(stream))
        handler.setStream(other)
        assert handler.format(_record(logging.ERROR)) == "boom"
        handler.setStream(stream)
        assert handler.format(_record(logging.ERROR)) == "<3>boom"


def test_the_module_is_standard_library_only():
    """``scripts/_journal.py`` loads this file under ``/usr/bin/python3`` with no
    venv; a third-party import here would break the disk units and the hook."""
    tree = ast.parse(JOURNAL_PY.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports: it is loaded by path"
            imported.add(node.module.split(".")[0])
    assert imported, "parsed no imports"
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
