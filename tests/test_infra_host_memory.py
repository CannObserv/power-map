"""The host-wide memory config that makes #537's unit settings effective (#541).

cgroup v2 protection is hierarchical: a child keeps at most what every ancestor
grants, and this host mounts cgroup2 without `memory_recursiveprot`, so
`power-map.service`'s `MemoryLow=` protects nothing unless `system.slice`
claims at least as much. The slice drop-in is that claim; it has to cover the
sum of every unit's `MemoryLow=`, or the newest claim silently gets nothing.

`vm.min_free_kbytes` keeps a reserve for atomic allocations — with no swap,
their failure in unrelated processes is how the 2026-09-16 sibling-VM outage
presented.

The kernel OOM killer is the only killer (#588). earlyoom was retired: every
heavy consumer here is a session, which exe.dev starts at `oom_score_adj` -1000
and which earlyoom skips just as the kernel does (#563), so all it could add
was an earlier trigger (<=10 % available, page cache counted) that reached
production before the kernel would have killed anything. Its one real job,
keeping `tailscaled` alive, moved to a unit drop-in the kernel honours: since
#568 every DNS lookup, the DB host's included, goes through it, so it ranks
below the API.

The -1000 premise is exe.dev's (#586) and is pinned live rather than assumed. A
login shell raises itself to 0 from `~/.profile` (docs/COMMANDS.md); that happens
below `sshd-session`, which the live check reads, so the pin still tracks exe.dev.
"""

import os
import re
import socket
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra"
SLICE_DROPIN = INFRA / "system.slice.d" / "90-power-map-memory.conf"
SYSCTL = INFRA / "sysctl.d" / "90-power-map-memory.conf"
TAILSCALED_DROPIN = INFRA / "tailscaled.service.d" / "90-power-map-oom.conf"
API_UNIT = INFRA / "power-map.service"
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


def _oom_score_adj(path: Path) -> int:
    [value] = _values(path, "OOMScoreAdjust")
    return int(value)


def test_tailscaled_dropin_is_a_service_section():
    assert "[Service]" in TAILSCALED_DROPIN.read_text().splitlines()


def test_tailscaled_ranks_below_the_api_it_resolves_for():
    """Killing tailscaled frees ~52 MiB and takes the API's DNS with it (#568)."""
    assert OOM_FLOOR < _oom_score_adj(TAILSCALED_DROPIN) < _oom_score_adj(API_UNIT)


def test_earlyoom_stays_retired():
    """#588: nothing on this host is both reachable and worth killing early."""
    assert not (INFRA / "default" / "earlyoom").exists()
    install = re.compile(r"apt(-get)?\s+install\b.*\bearlyoom")
    assert not install.search(COMMANDS_MD.read_text())


def test_every_host_file_has_an_install_line():
    """The host runs copies; a file COMMANDS.md never installs is never applied."""
    install_lines = [
        ln for ln in COMMANDS_MD.read_text().splitlines() if ln.startswith("sudo install ")
    ]
    host_files = [
        f.relative_to(INFRA.parent).as_posix()
        for d in INFRA.iterdir()
        if d.is_dir() and d.name != "terraform"
        for f in d.iterdir()
    ]

    assert host_files
    for rel in host_files:
        assert any(rel in ln for ln in install_lines), f"{rel} has no install line"


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
    """#586's premise: the kernel's order in docs/COMMANDS.md assumes it."""
    adj = _session_root_adj(Path("/proc"), os.getpid())
    if adj is None:
        pytest.skip("not run from an exe.dev session")
    assert adj == OOM_FLOOR, (
        f"this session's root reads oom_score_adj={adj}, not {OOM_FLOOR}: exe.dev no "
        "longer exempts sessions here — #586 may be fixed upstream; re-measure the "
        "kernel's victim order in docs/COMMANDS.md and revisit #586's mitigation"
    )
