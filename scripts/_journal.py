"""Journal priority for the stdlib scripts' log lines (#690, #692).

journald files a plain stderr line at ``info``, so ``journalctl -p warning``
never sees a WARNING. When stderr is the journal (``$JOURNAL_STREAM`` names its
``st_dev:st_ino``), each line starts with its syslog ``<N>``, which systemd's
default ``SyslogLevelPrefix=yes`` turns into the entry's priority. Off the
journal the lines read plain.

**Standard library only, imported as a sibling.** Its users run as
``/usr/bin/python3 /home/exedev/power-map/scripts/<name>.py`` with no venv,
where the script's own directory is ``sys.path[0]``; under pytest or
``python -m`` they're ``scripts.<name>``, and the package import serves. Each
user imports it the same way, sibling first, so the units never take the
fallback::

    try:  # run as a file (the units): the script's directory is sys.path[0]
        import _journal
    except ImportError:  # imported as scripts.<name> (tests, python -m)
        from scripts import _journal

``python3 -I`` / ``-P`` or ``PYTHONSAFEPATH`` drop ``sys.path[0]``, and with
them this import; no unit or env file sets them.
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
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


class _JournalFormatter(logging.Formatter):
    """Prefixes each line with its ``<N>`` syslog priority."""

    def format(self, record: logging.LogRecord) -> str:
        priority = _SYSLOG_PRIORITY.get(record.levelno, 6)
        return f"<{priority}>{super().format(record)}"


def _is_journal(stream: TextIO, environ: Mapping[str, str]) -> bool:
    """True when ``stream`` is the journal stream systemd names in ``$JOURNAL_STREAM``."""
    try:
        st = os.fstat(stream.fileno())
    except (OSError, ValueError, io.UnsupportedOperation):
        return False
    return environ.get("JOURNAL_STREAM") == f"{st.st_dev}:{st.st_ino}"


def log_handler(stream: TextIO, environ: Mapping[str, str]) -> logging.Handler:
    """A stderr handler whose lines carry their journal priority when on the journal."""
    handler = logging.StreamHandler(stream)
    cls = _JournalFormatter if _is_journal(stream, environ) else logging.Formatter
    handler.setFormatter(cls(LOG_FORMAT))
    return handler
