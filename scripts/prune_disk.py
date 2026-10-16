"""Weekly root-disk prune (issue #665): what grows here and nothing else removes.

Wired to ``infra/power-map-disk-prune.timer`` (Sundays 05:30 UTC), run as
``exedev``. ``/`` is one 19G volume shared by ``/home`` and ``/tmp``, and three
things kept refilling it after every manual cleanup:

- **VS Code server builds**, ``~/.vscode-server/cli/servers/Stable-*``: one
  ~650M build per client update, about weekly.
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

**Standard library only, run by ``/usr/bin/python3`` straight from the file**,
so it works on a disk too full for ``uv run``, and so ``uv cache prune`` never
waits on a cache lock held by the ``uv run`` that launched it.

Dry run by default (#402): it prints the plan with sizes. ``--execute`` removes
and runs the cache commands.

Exit codes: 0 = done (or nothing to do); 1 = a removal or a cache command
failed (the rest still ran); 2 = usage error.

Usage:
    /usr/bin/python3 scripts/prune_disk.py              # the plan, sizes, no change
    /usr/bin/python3 scripts/prune_disk.py --execute    # remove, then the caches
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

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
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    home: Path | None = None,
    live_fn: Callable[[], set[str]] = live_paths,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    settings: Path = SETTINGS,
) -> int:
    """Plan, and with ``--execute`` prune; return the exit code."""
    args = build_parser().parse_args(argv)
    home = home or Path.home()
    machine = home / ".vscode-server" / "data" / "Machine" / "settings.json"
    keep_specs = pinned_specs(settings) | launch_specs(machine)
    targets = plan(home, live_fn(), keep_specs)
    verb = "removing" if args.execute else "would remove"
    total = 0
    for path in targets:
        size = _size(path)
        total += size
        logger.info("%s %8s  %s", verb, _human(size), path)
    logger.info("%s %s across %d paths", verb, _human(total), len(targets))
    if not args.execute:
        for cmd in CACHE_COMMANDS:
            logger.info("would run %s", " ".join(cmd))
        logger.info("dry run: nothing removed; pass --execute to prune")
        return 0
    ok = all([_remove(p) for p in targets])
    ok = _run_cache_commands(run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
