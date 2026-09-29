"""The host-wide memory config that makes #537's unit settings effective (#541).

cgroup v2 protection is hierarchical: a child keeps at most what every ancestor
grants, and this host mounts cgroup2 without `memory_recursiveprot`, so
`power-map.service`'s `MemoryLow=` protects nothing unless `system.slice`
claims at least as much. The slice drop-in is that claim; it has to cover the
sum of every unit's `MemoryLow=`, or the newest claim silently gets nothing.

`vm.min_free_kbytes` keeps a reserve for atomic allocations — with no swap,
their failure in unrelated processes is how the 2026-09-16 sibling-VM outage
presented.

earlyoom ranks by `oom_score`, and on its defaults its first pick here is the
exedev session `dbus-daemon` (badness 800, 5 MiB — `oom_score_adj` 200), then
`systemd --user` and `(sd-pam)` (100): the user manager goes before Qdrant
(686) and frees nothing. `--avoid` restores the intended order. Qdrant and
Ollama were `--prefer`red until #568 retired the local SocratiCode store.

Sessions are not on that list at all. earlyoom 1.7 skips `oom_score_adj` -1000
exactly as the kernel does (`kill.c:242-253`), and exe.dev starts sessions at
-1000 here; its `-d` table prints badness *before* that skip, which is how this
repo once documented sessions as last-resort candidates (#563,
gregoryfoster/skills#331). The notes are held to the correction, and the
premise — sessions at -1000 on this host — is pinned live rather than assumed.
"""

import os
import re
import socket
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra"
SLICE_DROPIN = INFRA / "system.slice.d" / "90-power-map-memory.conf"
SYSCTL = INFRA / "sysctl.d" / "90-power-map-memory.conf"
EARLYOOM_DEFAULTS = INFRA / "default" / "earlyoom"
COMMANDS_MD = INFRA.parent / "docs" / "COMMANDS.md"

HOST = "power-map"
OOM_FLOOR = -1000
# What exe.dev starts a session from; the -1000 is theirs, inherited or not.
SESSION_PARENTS = frozenset({"exe-init", "sshd"})

KERNEL_DEFAULT_MIN_FREE_KBYTES = 10993  # measured on this host, 2026-09-19
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _bytes(value: str) -> int:
    """systemd's memory-size syntax (base 1024), percentages refused."""
    m = re.fullmatch(r"(\d+)([KMGT]?)", value.strip())
    assert m, f"unparseable memory size {value!r}"
    return int(m.group(1)) * _UNITS[m.group(2)]


def _values(path: Path, key: str) -> list[str]:
    return [
        ln.split("=", 1)[1].strip()
        for ln in path.read_text().splitlines()
        if ln.strip().startswith(f"{key}=")
    ]


def test_the_slice_dropin_is_a_slice_section():
    assert "[Slice]" in SLICE_DROPIN.read_text().splitlines()


def test_the_slice_grants_at_least_every_units_memory_low_claim():
    claims = {
        unit.name: _bytes(v) for unit in INFRA.glob("*.service") for v in _values(unit, "MemoryLow")
    }
    [grant] = _values(SLICE_DROPIN, "MemoryLow")

    assert claims, "no unit claims MemoryLow= — the drop-in has nothing to cover"
    assert _bytes(grant) >= sum(claims.values()), (
        f"system.slice MemoryLow={grant} covers less than the units' claims {claims}"
    )


def test_the_sysctl_file_raises_min_free_kbytes_and_sets_nothing_else():
    lines = [
        ln.strip()
        for ln in SYSCTL.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    keys = {ln.split("=", 1)[0].strip(): ln.split("=", 1)[1].strip() for ln in lines}

    assert set(keys) == {"vm.min_free_kbytes"}
    assert int(keys["vm.min_free_kbytes"]) > KERNEL_DEFAULT_MIN_FREE_KBYTES


def _earlyoom_args() -> list[str]:
    [value] = _values(EARLYOOM_DEFAULTS, "EARLYOOM_ARGS")
    assert value.startswith('"') and value.endswith('"')
    return value[1:-1].split()


def _flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def test_earlyoom_args_survive_systemd_word_splitting():
    """The unit passes `$EARLYOOM_ARGS` unquoted: systemd splits on whitespace and
    unquotes/unescapes itself, so a quote or backslash inside it is a trap."""
    for arg in _earlyoom_args():
        assert not set(arg) & {"'", '"', "\\"}, f"{arg!r} depends on quoting"


def test_earlyoom_prefers_nothing_once_the_local_store_is_retired():
    """`--prefer` named Qdrant and Ollama, the heavy restartable consumers, until
    #568 moved SocratiCode to co-index. Nothing left here is both heavy and
    safely restartable, so the kernel's own order stands after `--avoid`."""
    assert "--prefer" not in _earlyoom_args()


def test_earlyoom_avoids_the_user_manager_that_outranks_them():
    avoid = re.compile(_flag(_earlyoom_args(), "--avoid"))

    for comm in ("systemd", "(sd-pam)", "dbus-daemon"):
        assert avoid.search(comm), comm
    for comm in ("qdrant", "ollama", "systemd-logind", "systemd-timesyncd"):
        assert not avoid.search(comm), comm


def test_earlyoom_avoids_tailscaled_which_answers_every_dns_lookup():
    """`tailscale up --accept-dns` points /etc/resolv.conf at MagicDNS (#568), so the
    DB host resolves through tailscaled; at badness ~672 it sits level with Ollama."""
    avoid = re.compile(_flag(_earlyoom_args(), "--avoid"))

    assert avoid.search("tailscaled")
    assert not avoid.search("tailscale")


def _earlyoom_notes() -> dict[str, str]:
    """Every passage that states earlyoom's victim order, keyed by where it lives."""
    comment = "\n".join(
        ln for ln in EARLYOOM_DEFAULTS.read_text().splitlines() if ln.lstrip().startswith("#")
    )
    commands = COMMANDS_MD.read_text()
    start = commands.index("earlyoom ranks by `oom_score`")
    return {
        "infra/default/earlyoom": comment,
        "docs/COMMANDS.md": commands[start : commands.index("\n---", start)],
    }


def test_earlyoom_notes_never_call_a_minus_1000_session_a_candidate():
    """earlyoom 1.7 exempts -1000 as the kernel does; a -1000 row in `-d` is pre-skip."""
    stale = re.compile(r"not exempt|still\s+candidates|keeps\s+`?-1000`?\s+processes", re.I)
    for where, text in _earlyoom_notes().items():
        assert not stale.search(text), f"{where} still says earlyoom can take a -1000 session"


def test_earlyoom_notes_name_what_makes_a_session_process_reachable():
    for where, text in _earlyoom_notes().items():
        assert "choom -n 500" in text, f"{where} does not say how a session process becomes one"


def test_the_dry_run_check_reads_the_verdict_not_the_badness_column():
    text = _earlyoom_notes()["docs/COMMANDS.md"]

    assert "--dryrun" in text
    assert "new victim" in text and "sending" in text
    # Measured: plain `timeout` in a pipeline signalled the calling shell (exit 130).
    assert "timeout --foreground" in text


def _session_root_adj(proc: Path, pid: int) -> int | None:
    """`oom_score_adj` of the process exe.dev started `pid`'s session from.

    Walks the ancestry to the first process whose parent is in
    `SESSION_PARENTS` and reads that one, not `pid`: a leaf can be `choom`'d,
    the session root cannot. `None` when no ancestor is a session — a systemd
    unit, cron, a timer.
    """
    while pid > 1:
        status = (proc / str(pid) / "status").read_text()
        ppid = int(re.search(r"^PPid:\s*(\d+)", status, flags=re.MULTILINE).group(1))
        if ppid < 1:
            return None
        if (proc / str(ppid) / "comm").read_text().strip() in SESSION_PARENTS:
            return int((proc / str(pid) / "oom_score_adj").read_text())
        pid = ppid
    return None


def _fake_process(proc: Path, pid: int, ppid: int, comm: str, adj: int) -> None:
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "status").write_text(f"Name:\t{comm}\nPPid:\t{ppid}\n")
    (proc / str(pid) / "comm").write_text(f"{comm}\n")
    (proc / str(pid) / "oom_score_adj").write_text(f"{adj}\n")


def test_a_session_root_is_read_past_a_choomd_leaf(tmp_path: Path):
    _fake_process(tmp_path, 245, 1, "sshd", -1000)
    _fake_process(tmp_path, 2826, 245, "sshd-session", -1000)
    _fake_process(tmp_path, 2829, 2826, "bash", -1000)
    _fake_process(tmp_path, 900, 2829, "node", 500)  # launched under choom -n 500

    assert _session_root_adj(tmp_path, 900) == -1000


def test_a_session_at_zero_reports_zero(tmp_path: Path):
    _fake_process(tmp_path, 216, 1, "sshd", -1000)
    _fake_process(tmp_path, 700, 216, "bash", 0)  # notifier's shape (skills#303)
    _fake_process(tmp_path, 701, 700, "python3", 0)

    assert _session_root_adj(tmp_path, 701) == 0


def test_no_session_ancestor_is_none(tmp_path: Path):
    _fake_process(tmp_path, 1, 0, "systemd", 0)
    _fake_process(tmp_path, 300, 1, "systemd", 100)
    _fake_process(tmp_path, 301, 300, "python3", 0)

    assert _session_root_adj(tmp_path, 301) is None


@pytest.mark.skipif(socket.gethostname() != HOST, reason=f"not {HOST}")
def test_sessions_on_this_host_are_still_exempt():
    """The victim order in infra/default/earlyoom assumes it; nothing else checks."""
    adj = _session_root_adj(Path("/proc"), os.getpid())
    if adj is None:
        pytest.skip("not run from an exe.dev session")
    assert adj == OOM_FLOOR, (
        f"this session's root reads oom_score_adj={adj}, not {OOM_FLOOR}: exe.dev no "
        "longer exempts sessions here, so earlyoom's --prefer could reach them — "
        "revisit infra/default/earlyoom's victim order (#563, skills#331 §4)"
    )
