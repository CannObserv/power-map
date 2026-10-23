"""Daily root-disk prune (issue #665): what grows here and nothing else removes.

Wired to ``infra/power-map-disk-prune.timer`` (daily 05:30 UTC; weekly until
#690, when two VS Code builds in one week outran it), run as ``exedev``. ``/``
is one 19G volume shared by ``/home`` and ``/tmp``, and three things kept
refilling it after every manual cleanup:

- **VS Code server builds**, ``~/.vscode-server/cli/servers/Stable-*``: one
  ~650M build per client update, weekly or faster.
- **Claude Code extension versions**, ``~/.vscode-server/extensions``: ~255M per
  release. VS Code marks a superseded version in ``.obsolete`` but deletes it
  only when the server restarts, and the server runs for weeks.
- **npx installs**, ``~/.npm/_npx/<hash>``: ~460M per SocratiCode spec. npm's
  ``cache clean`` never touches them.

Then ``npm cache clean --force`` and ``uv cache prune --force`` (unreachable
objects only; a wheel a lockfile needs is re-downloaded on the next sync).
``--force`` skips uv's in-use lock, which production's ``uv run uvicorn`` holds
permanently; the cost is a narrow race with a ``uv sync`` mid-install, which
then fails and is re-run.

**Anything a running process uses stays:** an exe, a cwd, a mapped file or an
open fd under the path, read from ``/proc`` and never from process arguments
(a session's shell command line carries ``GH_TOKEN``). On top of that, each
kind keeps what the next session reaches for: the newest server by VS Code's
``lru.json``, the newest ``code-*`` CLI, the extension version
``extensions.json`` registers, and the npx install of each pinned
``SOCRATICODE_SPEC``, the repo's and the VS Code machine setting's (npx names
the dir by a hash of the spec). An extension
host that loaded an obsolete version with no live binary loses that extension
until *Reload Window*; VS Code's own restart cleanup does the same.

**Anything touched within the grace window stays too** (``--grace-minutes``,
default 60, as usa-wa's ``DISK_GC_GRACE_MINUTES``; #669). Liveness misses an
install being written right now: a new server build or npx tree that nothing
runs from yet. "Touched" is the newest ``max(mtime, ctime)`` of the path and
every directory under it: VS Code's extractor restores archive mtimes (a CLI
landed 2026-10-04 reads mtime 09-30), and ctime can't be backdated. Directories
only, because each file created moves its parent's ctime, and a file still
being written is held open. A path that can't be read, or whose walk passes
100 000 directories, is withheld too. Every withheld path is logged.

**Standard library only, run by ``/usr/bin/python3`` straight from the file**,
so it works on a disk too full for ``uv run``, and so ``uv cache prune`` never
waits on a cache lock held by the ``uv run`` that launched it.

Each journal line carries its syslog ``<N>`` priority when stderr is the
journal (#690), so ``journalctl -p warning`` finds an unchecked or failed path;
the helper is duplicated from ``check_disk_headroom.py`` because neither file
imports from the repo, and one test holds both to the same output.

Dry run by default (#402): it prints the plan with sizes. ``--execute`` removes
and runs the cache commands.

Exit codes: 0 = done (or nothing to do); 1 = a removal or a cache command
failed, or a stale path could not be checked (the rest still ran); 2 = usage
error.

Usage:
    /usr/bin/python3 scripts/prune_disk.py              # the plan, sizes, no change
    /usr/bin/python3 scripts/prune_disk.py --execute    # remove, then the caches
"""

import argparse
import hashlib
import io
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

logger = logging.getLogger("prune_disk")

REPO = Path(__file__).resolve().parents[1]
SETTINGS = REPO / ".claude" / "settings.json"
# `uv cache prune` waits for every other uv process to release the cache, and
# production's `uv run uvicorn` never does, so it needs --force. It still only
# removes unreachable objects; the venvs keep their hardlinked copies.
CACHE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("npm", "cache", "clean", "--force"),
    ("uv", "cache", "prune", "--force"),
)
CACHE_TIMEOUT_SECONDS = 600
# usa-wa's DISK_GC_GRACE_MINUTES (CannObserv/usa-wa#394).
GRACE_MINUTES = 60
# ~100x the largest tree measured (an npx install: 1033 dirs, a 14 ms walk).
WALK_DIR_BOUND = 100_000


# --- what is live ---------------------------------------------------------------


def _maps_paths(text: str) -> set[str]:
    """File paths from a ``/proc/<pid>/maps``, `` (deleted)`` stripped."""
    paths = set()
    for line in text.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            paths.add(fields[5].removesuffix(" (deleted)"))
    return paths


def live_paths(proc: Path = Path("/proc")) -> set[str]:
    """Every path a readable process runs from, sits in, maps or holds open."""
    live: set[str] = set()
    for pid in proc.iterdir():
        if not pid.name.isdigit():
            continue
        for link in (pid / "exe", pid / "cwd"):
            try:
                live.add(os.readlink(link))
            except OSError:
                pass
        try:
            for fd in (pid / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("/"):
                    live.add(target)
        except OSError:
            pass
        try:
            live |= _maps_paths((pid / "maps").read_text())
        except OSError:
            pass
    return live


def in_use(path: Path, live: set[str]) -> bool:
    """True when a live path is ``path`` itself or anything under it."""
    root = str(path)
    prefix = root + "/"
    return any(p == root or p.startswith(prefix) for p in live)


# --- what each kind keeps -------------------------------------------------------


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def vscode_servers(vscode: Path, live: set[str]) -> list[Path]:
    """Server builds that are neither running nor the newest."""
    servers = vscode / "cli" / "servers"
    if not servers.is_dir():
        return []
    builds = sorted(p for p in servers.iterdir() if p.is_dir() and p.name.startswith("Stable-"))
    if not builds:
        return []
    lru = _read_json(servers / "lru.json")
    on_disk = {p.name: p for p in builds}
    ordered = [on_disk[n] for n in lru if n in on_disk] if isinstance(lru, list) else []
    newest = ordered[0] if ordered else max(builds, key=lambda p: p.stat().st_mtime)
    return [p for p in builds if p != newest and not in_use(p, live)]


def vscode_clis(vscode: Path, live: set[str]) -> list[Path]:
    """``code-*`` CLIs that are neither running nor the newest."""
    if not vscode.is_dir():
        return []
    clis = sorted(p for p in vscode.iterdir() if p.is_file() and p.name.startswith("code-"))
    if not clis:
        return []
    newest = max(clis, key=lambda p: p.stat().st_mtime)
    return [p for p in clis if p != newest and not in_use(p, live)]


def obsolete_extensions(vscode: Path, live: set[str]) -> list[Path]:
    """Extension dirs VS Code marked obsolete, unless registered or in use.

    An unreadable ``extensions.json`` removes nothing: without it there is no
    telling the registered version from the rest.
    """
    ext = vscode / "extensions"
    registry = _read_json(ext / "extensions.json")
    obsolete = _read_json(ext / ".obsolete")
    if not isinstance(registry, list) or not isinstance(obsolete, dict):
        return []
    registered = {e.get("relativeLocation") for e in registry if isinstance(e, dict)}
    found = []
    for name in sorted(obsolete):
        if name in registered or "/" in name or name in ("", ".", ".."):
            continue
        path = ext / name
        if path.is_dir() and not path.is_symlink() and not in_use(path, live):
            found.append(path)
    return found


def npx_dir_name(spec: str) -> str:
    """The ``_npx`` dir npm uses for one package spec (libnpmexec's hash)."""
    return hashlib.sha512(spec.encode()).hexdigest()[:16]


def pinned_specs(settings: Path) -> set[str]:
    """``SOCRATICODE_SPEC`` from the repo's Claude settings, if it states one."""
    data = _read_json(settings)
    env = data.get("env") if isinstance(data, dict) else None
    spec = env.get("SOCRATICODE_SPEC") if isinstance(env, dict) else None
    return {spec} if isinstance(spec, str) and spec else set()


def launch_specs(machine_settings: Path) -> set[str]:
    """``SOCRATICODE_SPEC`` from VS Code's ``claudeCode.environmentVariables``.

    That machine setting is what reaches the session server's launch; the
    repo's value is only what preflight compares it against (#559). Keeping
    both protects the next session's install even when the two diverge.
    """
    data = _read_json(machine_settings)
    env = data.get("claudeCode.environmentVariables") if isinstance(data, dict) else None
    if not isinstance(env, list):
        return set()
    return {
        e["value"]
        for e in env
        if isinstance(e, dict)
        and e.get("name") == "SOCRATICODE_SPEC"
        and isinstance(e.get("value"), str)
        and e["value"]
    }


def npx_installs(npm: Path, live: set[str], keep_specs: Iterable[str]) -> list[Path]:
    """npx install dirs that are neither in use nor the pinned spec's."""
    npx = npm / "_npx"
    if not npx.is_dir():
        return []
    keep = {npx_dir_name(s) for s in keep_specs}
    return [
        p
        for p in sorted(npx.iterdir())
        if p.is_dir() and not p.is_symlink() and p.name not in keep and not in_use(p, live)
    ]


def plan(home: Path, live: set[str], keep_specs: set[str]) -> list[Path]:
    """Everything this run would remove, in a stable order."""
    vscode = home / ".vscode-server"
    return [
        *vscode_servers(vscode, live),
        *vscode_clis(vscode, live),
        *obsolete_extensions(vscode, live),
        *npx_installs(home / ".npm", live, keep_specs),
    ]


# --- what is still settling (#669) ----------------------------------------------


def _touched(st: os.stat_result) -> float:
    return max(st.st_mtime, st.st_ctime)


def withhold_reason(path: Path, cutoff: float, bound: int = WALK_DIR_BOUND) -> str | None:
    """Why ``path`` must stay although stale, or None once it has settled.

    It stays when it or a directory under it was touched after ``cutoff``, and
    whenever that can't be told: an unreadable entry, or a walk past ``bound``.
    """
    try:
        root = path.lstat()
    except OSError as exc:
        return f"unreadable ({exc.strerror})"
    if not stat.S_ISDIR(root.st_mode):
        return "recently touched" if _touched(root) > cutoff else None
    errors: list[OSError] = []
    for seen, (dirpath, _dirs, _files) in enumerate(os.walk(path, onerror=errors.append), 1):
        if seen > bound:
            return f"walk bound hit (>{bound} dirs)"
        try:
            if _touched(os.lstat(dirpath)) > cutoff:
                return "recently touched"
        except OSError as exc:
            errors.append(exc)
    if errors:
        return f"unreadable ({errors[0].strerror})"
    return None


def split_recent(
    paths: Iterable[Path], cutoff: float, bound: int = WALK_DIR_BOUND
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """``(remove, withheld)``: the settled paths, and each other one with its reason."""
    remove: list[Path] = []
    withheld: list[tuple[Path, str]] = []
    for path in paths:
        reason = withhold_reason(path, cutoff, bound)
        if reason is None:
            remove.append(path)
        else:
            withheld.append((path, reason))
    return remove, withheld


# --- journal priority (#690; as check_disk_headroom.py) --------------------------

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


# --- doing it -------------------------------------------------------------------


def _size(path: Path) -> int:
    if path.is_file():
        return path.lstat().st_blocks * 512
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for name in filenames + dirnames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
            except OSError:
                pass
    return total


def _human(n: int) -> str:
    value = float(n)
    for unit in ("B", "K", "M"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


def _remove(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        logger.error("could not remove %s: %s", path, exc)
        return False
    return True


def _run_cache_commands(run: Callable[..., subprocess.CompletedProcess]) -> bool:
    ok = True
    for cmd in CACHE_COMMANDS:
        try:
            result = run(
                list(cmd),
                capture_output=True,
                text=True,
                timeout=CACHE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("%s failed to run: %s", " ".join(cmd), exc)
            ok = False
            continue
        if result.returncode != 0:
            logger.error("%s exited %d", " ".join(cmd), result.returncode)
            ok = False
        else:
            logger.info("ran %s", " ".join(cmd))
    return ok


def build_parser() -> argparse.ArgumentParser:
    """The CLI; the module docstring is its help, unreflowed (#509)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--execute", action="store_true", help="remove and run the cache commands (default: plan)"
    )
    parser.add_argument(
        "--grace-minutes",
        type=_minutes,
        default=GRACE_MINUTES,
        help=f"keep paths touched this recently; 0 disables (default: {GRACE_MINUTES})",
    )
    return parser


def _minutes(value: str) -> int:
    try:
        minutes = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of minutes") from None
    if minutes < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return minutes


def main(
    argv: Sequence[str] | None = None,
    *,
    home: Path | None = None,
    live_fn: Callable[[], set[str]] = live_paths,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    settings: Path = SETTINGS,
    clock: Callable[[], float] = time.time,
) -> int:
    """Plan, and with ``--execute`` prune; return the exit code."""
    args = build_parser().parse_args(argv)
    home = home or Path.home()
    machine = home / ".vscode-server" / "data" / "Machine" / "settings.json"
    keep_specs = pinned_specs(settings) | launch_specs(machine)
    targets = plan(home, live_fn(), keep_specs)
    withheld: list[tuple[Path, str]] = []
    if args.grace_minutes:
        targets, withheld = split_recent(targets, clock() - args.grace_minutes * 60)
    for path, reason in withheld:
        level = logging.INFO if reason == "recently touched" else logging.WARNING
        logger.log(level, "withheld: %s  %s", reason, path)
    verb = "removing" if args.execute else "would remove"
    total = 0
    for path in targets:
        size = _size(path)
        total += size
        logger.info("%s %8s  %s", verb, _human(size), path)
    logger.info(
        "%s %s across %d paths; %d withheld", verb, _human(total), len(targets), len(withheld)
    )
    if not args.execute:
        for cmd in CACHE_COMMANDS:
            logger.info("would run %s", " ".join(cmd))
        logger.info("dry run: nothing removed; pass --execute to prune")
        return 0
    # An unchecked path is what rmtree used to fail on; it still fails the run.
    ok = all(reason == "recently touched" for _, reason in withheld)
    ok = all([_remove(p) for p in targets]) and ok
    ok = _run_cache_commands(run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, handlers=[log_handler(sys.stderr, os.environ)])
    sys.exit(main())
