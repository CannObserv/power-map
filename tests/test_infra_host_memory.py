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
(686) and frees nothing. `--prefer`/`--avoid` restore the intended order —
Qdrant, then Ollama, both restartable.
"""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1] / "infra"
SLICE_DROPIN = INFRA / "system.slice.d" / "90-power-map-memory.conf"
SYSCTL = INFRA / "sysctl.d" / "90-power-map-memory.conf"
EARLYOOM_DEFAULTS = INFRA / "default" / "earlyoom"

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


def test_earlyoom_prefers_the_restartable_heavyweights():
    prefer = re.compile(_flag(_earlyoom_args(), "--prefer"))

    assert prefer.search("qdrant") and prefer.search("ollama")
    assert not prefer.search("uvicorn") and not prefer.search("python3")


def test_earlyoom_avoids_the_user_manager_that_outranks_them():
    avoid = re.compile(_flag(_earlyoom_args(), "--avoid"))

    for comm in ("systemd", "(sd-pam)", "dbus-daemon"):
        assert avoid.search(comm), comm
    for comm in ("qdrant", "ollama", "systemd-logind", "systemd-timesyncd"):
        assert not avoid.search(comm), comm
