"""Journal priority for log lines: the one rule (#690, #692, #704).

journald files a plain stdout/stderr line at ``info``, so ``journalctl -p
warning`` never sees a WARNING. When a handler's own stream is the journal
(``$JOURNAL_STREAM`` names its ``st_dev:st_ino``), each line starts with its
syslog ``<N>``; systemd's default ``SyslogLevelPrefix=yes`` turns that into the
entry's priority and strips it, so a JSON line stays JSON in ``MESSAGE``. Off
the journal (a terminal, a pipe, tests) the lines are unchanged.

``$JOURNAL_STREAM`` names one stream: a unit whose stderr is the journal and
whose stdout is not gets no prefix on stdout, because each handler checks the
stream it writes to.

**Standard library only, loaded by path.** ``configure_logging()`` and
``log_config.json`` (uvicorn) use it as ``src.core.journal``; the venv-less
``/usr/bin/python3`` scripts load this file by path through
``scripts/_journal.py``, so it must never import beyond the standard library
(guard: ``tests/core/test_journal.py``).
"""

import io
import logging
import os
from collections.abc import Mapping
from typing import TextIO

# logging level -> syslog priority, for systemd's SyslogLevelPrefix=.
_SYSLOG_PRIORITY = {
    logging.CRITICAL: 2,
    logging.ERROR: 3,
    logging.WARNING: 4,
    logging.INFO: 6,
    logging.DEBUG: 7,
}


def _is_journal(stream: TextIO, environ: Mapping[str, str]) -> bool:
    """True when ``stream`` is the journal stream systemd names in ``$JOURNAL_STREAM``."""
    try:
        st = os.fstat(stream.fileno())
    except (OSError, ValueError, io.UnsupportedOperation):
        return False
    return environ.get("JOURNAL_STREAM") == f"{st.st_dev}:{st.st_ino}"


class JournalStreamHandler(logging.StreamHandler):
    """A ``StreamHandler`` whose lines carry their ``<N>`` when its stream is the journal.

    ``environ`` defaults to the process environment, so dictConfig can build it
    from ``stream`` alone. A level between the named ones reads as info.
    """

    def __init__(self, stream: TextIO | None = None, environ: Mapping[str, str] | None = None):
        """Bind the stream and check, once, whether it is the journal."""
        self._environ = os.environ if environ is None else environ
        super().__init__(stream)
        self._on_journal = _is_journal(self.stream, self._environ)

    def setStream(self, stream: TextIO) -> TextIO | None:  # noqa: N802 - logging's API
        """Swap the stream and re-check it against ``$JOURNAL_STREAM``."""
        old = super().setStream(stream)
        self._on_journal = _is_journal(self.stream, self._environ)
        return old

    def format(self, record: logging.LogRecord) -> str:
        """The formatter's line, prefixed with its syslog priority on the journal."""
        line = super().format(record)
        if not self._on_journal:
            return line
        return f"<{_SYSLOG_PRIORITY.get(record.levelno, 6)}>{line}"
