"""Regression tests: JSON log records carry timestamp, level, and logger name,
and uvicorn's own loggers share the app's JSON formatter (skills#69, skills#81).
"""

import json
import logging
import logging.config
from pathlib import Path

from src.core.logging import (
    ColorMessageFilter,
    build_json_formatter,
    configure_logging,
    get_logger,
)

LOG_CONFIG_PATH = Path("src/core/log_config.json")


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
