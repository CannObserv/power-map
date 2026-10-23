"""Root-disk headroom guard (issue #665): alert before ``/`` fills, not after.

Wired to ``infra/power-map-disk-headroom.timer`` (every 10 minutes). ``/``,
``/home`` and ``/tmp`` share one 19G volume on the production VM, and it hit
100% on 2026-09-25 and 98% on 2026-10-14 and 2026-10-15. Each time the first
signal was a failing agent session; a full root also starves
``power-map.service``, its timers and journald.

The percentage is ``df``'s ``Use%``: used over used-plus-available, rounded up.
Root's reserved blocks count against headroom, because neither the service nor
a session can write them.

On an alert the journal also gets the sizes of the known growth paths (pytest
basetemps, the VS Code server, the npm and uv caches, session data), largest
first; that list is most of the triage. The walk only happens on an alert.

**Standard library only, run by ``/usr/bin/python3`` straight from the file.**
A nearly full disk is exactly when ``uv run`` may fail to write what it needs,
so this file imports nothing from the repo and logs with plain ``logging`` to
stderr (the journal), as ``status_checkin.py`` does.

**Hysteresis (#690).** It alerts at ``--alert-at`` (85) and clears only at or
below ``--clear-at`` (80); a reading between the two keeps the previous state.
One pre-commit test run holds ~0.5G under ``/tmp`` until the next, so with the
baseline just under 85% every commit crossed the line both ways, and each
crossing was its own page and clear. The state is one line (``alert`` or
``ok``) in ``$STATE_DIRECTORY/state``, which the unit's ``StateDirectory=``
provides. A missing, unreadable or unrecognised file reads as ``ok``; with no
``$STATE_DIRECTORY`` (a run by hand) nothing is read or written, so the band
reads ``ok``. A held alert exits 3 again, so the unit keeps checking in
``low-headroom`` and ``missing`` means what it did.

**Journal priority matches the level (#690).** journald files a plain stderr
line at ``info``, so ``journalctl -p err`` never saw an alert. When stderr is
the journal (``$JOURNAL_STREAM`` names it), each line starts with its syslog
``<N>``, which systemd's default ``SyslogLevelPrefix=yes`` turns into the
entry's priority. Off the journal the lines read plain.

Exit codes: 0 = ok (below the alert line and not held); 3 = alerting, at or
above the alert line or held in the band (the unit checks in ``low-headroom``;
the first ``ok`` clears the fault); 2 = usage error.

Usage:
    /usr/bin/python3 scripts/check_disk_headroom.py                # alert 85%, clear 80%
    /usr/bin/python3 scripts/check_disk_headroom.py --alert-at 1   # prove it fires
"""

import argparse
import io
import logging
import math
import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

logger = logging.getLogger("check_disk_headroom")

DEFAULT_PATH = "/"
DEFAULT_ALERT_AT = 85
DEFAULT_CLEAR_AT = 80
# One line, ``alert`` or ``ok``, under the unit's StateDirectory= (#690).
STATE_FILE = "state"
ALERT = "alert"
OK = "ok"
HOME = Path("/home/exedev")
# What has filled this disk before (#665's table), checked only on an alert.
CONSUMER_PATHS = (
    Path("/tmp/pytest-of-exedev"),
    HOME / ".vscode-server",
    HOME / ".npm",
    HOME / ".cache",
    HOME / ".claude",
    HOME / ".socraticode",
    HOME / "power-map",
)


@dataclass(frozen=True)
class Usage:
    """One filesystem's usage as ``df`` reports it."""

    used_bytes: int
    avail_bytes: int
    percent: int


def usage(path: str, statvfs: Callable[[str], os.statvfs_result] = os.statvfs) -> Usage:
    """``df``'s view of ``path``: used = total - free, percent rounded up."""
    st = statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    denominator = used + avail
    percent = math.ceil(100 * used / denominator) if denominator else 0
    return Usage(used_bytes=used, avail_bytes=avail, percent=percent)


def _tree_size(root: Path) -> int:
    """Allocated bytes under ``root`` on its own filesystem, symlinks not followed."""
    total = 0
    try:
        device = root.lstat().st_dev
    except OSError:
        return 0
    # os.walk never descends a symlinked dir; pruning other devices keeps a
    # mount under one of these paths out of root's numbers.
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        same_device = []
        for name in filenames + dirnames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if st.st_dev != device:
                continue
            total += st.st_blocks * 512
            if name in dirnames:
                same_device.append(name)
        dirnames[:] = [d for d in dirnames if d in same_device]
    return total


def consumers(paths: Iterable[Path]) -> list[tuple[Path, int]]:
    """``(path, bytes)`` for each existing path, largest first."""
    sizes = [(p, _tree_size(p)) for p in paths if p.exists()]
    return sorted(sizes, key=lambda item: item[1], reverse=True)


def _human(n: int) -> str:
    """``df -h`` style: one decimal, binary units."""
    value = float(n)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


def _bounded(low: int, high: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {raw!r}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be {low}-{high}, got {value}")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    """The CLI; the module docstring is its help, unreflowed (#509)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--path", default=DEFAULT_PATH, help="filesystem to check (default /)")
    parser.add_argument(
        "--alert-at",
        type=_bounded(1, 100),
        default=DEFAULT_ALERT_AT,
        help=f"alert when Use%% is at or above this (default {DEFAULT_ALERT_AT})",
    )
    parser.add_argument(
        "--clear-at",
        type=_bounded(0, 99),
        default=None,
        help=(
            "once alerting, clear only when Use%% is at or below this; must be under "
            f"--alert-at (default {DEFAULT_CLEAR_AT}, or --alert-at minus 1 when lower)"
        ),
    )
    return parser


# --- state (#690) --------------------------------------------------------------


def state_path(environ: Mapping[str, str]) -> Path | None:
    """The state file under systemd's ``$STATE_DIRECTORY``, or None outside the unit."""
    directory = environ.get("STATE_DIRECTORY", "").split(":")[0]
    return Path(directory) / STATE_FILE if directory else None


def read_state(path: Path | None) -> str:
    """The previous state; anything but a readable ``alert`` line is ``ok``."""
    if path is None:
        return OK
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return OK
    return ALERT if text.strip() == ALERT else OK


def write_state(path: Path | None, state: str) -> None:
    """Record ``state`` atomically; a failure is a warning, never the run's result."""
    if path is None:
        return
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(state + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("could not record state %r in %s: %s", state, path, exc)


def decide(percent: int, alert_at: int, clear_at: int, previous: str) -> str:
    """``alert`` at or above the alert line, ``ok`` at or below the clear line,
    and the previous state in between."""
    if percent >= alert_at:
        return ALERT
    if percent <= clear_at:
        return OK
    return previous


# --- journal priority (#690) ---------------------------------------------------

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


def main(
    argv: Sequence[str] | None = None,
    usage_fn: Callable[[str], Usage] = usage,
    consumers_fn: Callable[[Iterable[Path]], list[tuple[Path, int]]] = consumers,
) -> int:
    """Check once; return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    clear_at = args.clear_at
    if clear_at is None:
        clear_at = min(DEFAULT_CLEAR_AT, args.alert_at - 1)
    elif clear_at >= args.alert_at:
        parser.error(f"--clear-at ({clear_at}) must be below --alert-at ({args.alert_at})")
    path = state_path(os.environ)
    previous = read_state(path)
    u = usage_fn(args.path)
    state = decide(u.percent, args.alert_at, clear_at, previous)
    summary = (
        f"{args.path} is {u.percent}% used, {_human(u.avail_bytes)} available "
        f"(alert at {args.alert_at}%, clears at {clear_at}%)"
    )
    if state != previous:
        write_state(path, state)
    if state == OK:
        if previous == ALERT:
            logger.info("headroom recovered: %s", summary)
        else:
            logger.info(summary)
        return 0
    held = " (held: not yet at the clear line)" if u.percent < args.alert_at else ""
    logger.error("low headroom%s: %s", held, summary)
    for consumer, size in consumers_fn(CONSUMER_PATHS):
        logger.error("  %8s  %s", _human(size), consumer)
    return 3


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, handlers=[log_handler(sys.stderr, os.environ)])
    sys.exit(main())
