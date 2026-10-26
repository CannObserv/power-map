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
them this import; no unit or env file sets them. The disk scripts would fail
on it; ``status_checkin.py`` catches it and checks in with plain lines.

**The rule itself lives in** ``src/core/journal.py`` (#704), shared with
``configure_logging()`` and uvicorn's ``log_config.json``. These scripts have
no venv and must not import the ``src`` package, so this module loads that one
stdlib-only file by path; there is no second copy to drift.
"""

import importlib.util
import logging
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def _load_rule():
    """``src/core/journal.py``, executed from its file: no ``src`` package, no venv."""
    path = Path(__file__).resolve().parent.parent / "src" / "core" / "journal.py"
    name = "_power_map_core_journal"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # importlib's recipe: inspect and pickle find it
    spec.loader.exec_module(module)
    return module


_rule = _load_rule()


def log_handler(stream: TextIO, environ: Mapping[str, str]) -> logging.Handler:
    """A stderr handler whose lines carry their journal priority when on the journal."""
    handler = _rule.JournalStreamHandler(stream, environ)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler
