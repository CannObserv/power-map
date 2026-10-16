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

Exit codes: 0 = below the threshold; 3 = at or above it (the unit checks in
``low-headroom``; the next green run's ``ok`` clears the fault); 2 = usage error.

Usage:
    /usr/bin/python3 scripts/check_disk_headroom.py                # alert at 85%
    /usr/bin/python3 scripts/check_disk_headroom.py --alert-at 1   # prove it fires
"""

import argparse
import logging
import math
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("check_disk_headroom")

DEFAULT_PATH = "/"
DEFAULT_ALERT_AT = 85
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


def _percent(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {raw!r}") from None
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"must be 1-100, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The CLI; the module docstring is its help, unreflowed (#509)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--path", default=DEFAULT_PATH, help="filesystem to check (default /)")
    parser.add_argument(
        "--alert-at",
        type=_percent,
        default=DEFAULT_ALERT_AT,
        help=f"alert when Use%% is at or above this (default {DEFAULT_ALERT_AT})",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    usage_fn: Callable[[str], Usage] = usage,
    consumers_fn: Callable[[Iterable[Path]], list[tuple[Path, int]]] = consumers,
) -> int:
    """Check once; return the exit code."""
    args = build_parser().parse_args(argv)
    u = usage_fn(args.path)
    summary = (
        f"{args.path} is {u.percent}% used, {_human(u.avail_bytes)} available "
        f"(alert at {args.alert_at}%)"
    )
    if u.percent < args.alert_at:
        logger.info(summary)
        return 0
    logger.error("low headroom: %s", summary)
    for path, size in consumers_fn(CONSUMER_PATHS):
        logger.error("  %8s  %s", _human(size), path)
    return 3


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
