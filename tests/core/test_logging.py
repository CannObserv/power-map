"""Regression tests: JSON log records carry timestamp, level, and logger name,
and uvicorn's own loggers share the app's JSON formatter (skills#69, skills#81).
On the journal each line carries its syslog ``<N>`` (#704).
"""

import json
import logging
import logging.config
import os
import sys
from pathlib import Path

import pytest

from src.core.journal import JournalStreamHandler
from src.core.logging import (
    ColorMessageFilter,
    build_json_formatter,
    configure_logging,
    get_logger,
)

LOG_CONFIG_PATH = Path("src/core/log_config.json")


@pytest.fixture
def journal_file(tmp_path: Path, monkeypatch):
    """A real file that ``$JOURNAL_STREAM`` names, as systemd names stdout.
    The test swaps it in as sys.stdout itself: pytest's capture re-sets
    sys.stdout when the call phase starts, undoing a fixture's patch."""
    with open(tmp_path / "stdout", "w") as stream:
        st = os.fstat(stream.fileno())
        monkeypatch.setenv("JOURNAL_STREAM", f"{st.st_dev}:{st.st_ino}")
        yield stream


@pytest.fixture
def saved_root():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers, root.level = saved_handlers, saved_level


def test_log_record_includes_structured_fields(capsys):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging()
        get_logger("src.some.module").warning("hello %s", "world")
    finally:
        root.handlers, root.level = saved_handlers, saved_level

    record = json.loads(capsys.readouterr().out)
    assert record["message"] == "hello world"
    assert record["level"] == "WARNING"
    assert record["logger"] == "src.some.module"
    assert "timestamp" in record


@pytest.mark.parametrize(
    ("level", "prefix"), [(logging.WARNING, "<4>"), (logging.ERROR, "<3>"), (logging.INFO, "<6>")]
)
def test_on_the_journal_each_json_line_carries_its_priority(
    level, prefix, journal_file, saved_root, monkeypatch
):
    """journald strips the ``<N>`` (SyslogLevelPrefix=yes), so what follows it
    must be the whole JSON object, alone on its line."""
    monkeypatch.setattr(sys, "stdout", journal_file)
    configure_logging()
    get_logger("src.some.module").log(level, "hello")
    journal_file.flush()

    line = Path(journal_file.name).read_text()
    assert line.startswith(prefix + "{") and line.endswith("}\n"), line
    record = json.loads(line.removeprefix(prefix))
    assert record["level"] == logging.getLevelName(level)
    assert record["message"] == "hello"


def test_off_the_journal_a_real_file_gets_plain_json(tmp_path: Path, monkeypatch, saved_root):
    """A terminal or a pipe: no ``$JOURNAL_STREAM``, so the line is bare JSON."""
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    with open(tmp_path / "stdout", "w") as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        configure_logging()
        get_logger("x").error("hello")
    assert json.loads((tmp_path / "stdout").read_text())["level"] == "ERROR"


def test_configure_logging_writes_through_the_journal_handler(saved_root):
    configure_logging()
    (handler,) = logging.getLogger().handlers
    assert isinstance(handler, JournalStreamHandler)
    assert handler.stream is sys.stdout


def test_uvicorn_log_config_is_valid_and_shares_formatter():
    """The uvicorn --log-config file wires uvicorn's loggers through the same
    formatter as the app, and dictConfig accepts it (a malformed file would
    fail the service at boot, not in review)."""
    config = json.loads(LOG_CONFIG_PATH.read_text())

    # Single source of truth: the file builds its formatter from the factory
    # configure_logging() also uses, not a duplicated fmt string.
    assert any(
        f.get("()") == "src.core.logging.build_json_formatter"
        for f in config["formatters"].values()
    )
    # All three uvicorn loggers must be present, else they keep the plain default.
    # Pin placement, not just effect: the color_message strip must sit on each
    # logger (mutates the record at its source), never on the stdout handler —
    # a handler-scoped strip resurrects the field the day a sink builds its
    # payload from record.__dict__ instead of a Formatter (OTel's LoggingHandler).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert name in config["loggers"]
        assert "strip_color_message" in config["loggers"][name]["filters"]
    # uvicorn's own lines (an unhandled ASGI exception is a uvicorn.error ERROR)
    # carry their journal priority through the same handler (#704).
    for handler in config["handlers"].values():
        assert handler["class"] == "src.core.journal.JournalStreamHandler"

    names = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        n: (
            logging.getLogger(n).handlers[:],
            logging.getLogger(n).propagate,
            logging.getLogger(n).level,
            logging.getLogger(n).filters[:],
        )
        for n in names
    }
    try:
        logging.config.dictConfig(config)  # raises on a malformed config
        for name in names:
            for handler in logging.getLogger(name).handlers:
                assert isinstance(handler, JournalStreamHandler), name
    finally:
        # Restore level AND filters: dictConfig sets root + uvicorn loggers to
        # INFO and attaches the strip filter, and leaking either into later
        # tests would be an order-dependent flake (same class as the #378 .level
        # restore).
        for n, (handlers, propagate, level, filters) in saved.items():
            lg = logging.getLogger(n)
            lg.handlers, lg.propagate, lg.level = handlers, propagate, level
            lg.filters = filters


def test_color_message_filter_strips_extra_from_record():
    """uvicorn attaches an ANSI-coloured duplicate of its lifecycle lines as
    `extra={"color_message": ...}`; the filter drops it at the record source so
    no handler ever serializes it. Asserting on the record itself (not the JSON
    output) proves the strip is sink-independent, not formatter-dependent."""
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Started server process [%d]",
        args=(4066888,),
        exc_info=None,
    )
    record.color_message = "Started server process [\033[36m%d\033[0m]"

    assert ColorMessageFilter().filter(record) is True
    assert not hasattr(record, "color_message")

    parsed = json.loads(build_json_formatter().format(record))
    assert parsed["message"] == "Started server process [4066888]"
    assert "color_message" not in parsed


def test_color_message_filter_passes_records_without_the_extra():
    """A record with no color_message is passed through untouched — filter()
    never drops a record."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="plain",
        args=(),
        exc_info=None,
    )
    assert ColorMessageFilter().filter(record) is True


def test_shared_formatter_renders_uvicorn_access_record():
    """A uvicorn.access record formats to JSON with the same fields as app logs
    — the request line lands in `message`, not a plain-text handler."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:0", "GET", "/health", "1.1", 200),
        exc_info=None,
    )
    parsed = json.loads(build_json_formatter().format(record))
    assert parsed["logger"] == "uvicorn.access"
    assert parsed["level"] == "INFO"
    assert parsed["message"] == '127.0.0.1:0 - "GET /health HTTP/1.1" 200'
    assert "timestamp" in parsed
