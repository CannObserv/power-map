"""Tests for scripts.check_disk_headroom — the root-disk headroom guard (#665).

The guard owns three things:

1. A percentage that matches ``df``'s ``Use%``: used over used-plus-available,
   rounded up, so root's reserved blocks never make the guard read lower than
   the number an operator sees.
2. Exit 3 at or above the threshold and nothing else; the unit's co-status
   check-in (#566) turns that into one alert and the next green run clears it.
3. A threshold low enough to fire on any disk, which is how the acceptance
   test ("fires on a test threshold") is run on the host.
"""

import os
from pathlib import Path

import pytest

from scripts.check_disk_headroom import DEFAULT_ALERT_AT, Usage, consumers, main, usage


class _Statvfs:
    """The four ``os.statvfs`` fields the guard reads."""

    def __init__(self, blocks: int, bfree: int, bavail: int, frsize: int = 4096) -> None:
        self.f_blocks = blocks
        self.f_bfree = bfree
        self.f_bavail = bavail
        self.f_frsize = frsize


def _fixed(blocks: int, bfree: int, bavail: int):
    return lambda _path: _Statvfs(blocks, bfree, bavail)


# --- usage ---------------------------------------------------------------------


def test_usage_matches_df_use_percent():
    # This host on 2026-10-16: 18,665 MB volume, 16,411 used, 1,286 available
    # (the ~968 MB gap is root's reserved blocks). df printed 93%.
    mb = 1024 * 1024 // 4096
    u = usage("/", statvfs=_fixed(18665 * mb, (18665 - 16411) * mb, 1286 * mb))
    assert u.percent == 93
    assert u.used_bytes == 16411 * 1024 * 1024
    assert u.avail_bytes == 1286 * 1024 * 1024


def test_usage_rounds_up_like_df():
    u = usage("/", statvfs=_fixed(blocks=1000, bfree=150, bavail=150))
    # 850 / 1000 = 85.0 exactly, so no rounding.
    assert u.percent == 85
    u = usage("/", statvfs=_fixed(blocks=1000, bfree=149, bavail=149))
    # 851 / 1000 = 85.1, which df shows as 86.
    assert u.percent == 86


def test_reserved_blocks_count_against_headroom():
    # 100 blocks free but only 50 available to a non-root user: the guard reads
    # what exedev and the service can actually write.
    u = usage("/", statvfs=_fixed(blocks=1000, bfree=100, bavail=50))
    assert u.percent == 95  # 900 / (900 + 50) = 94.7 → 95


def test_an_empty_filesystem_reads_zero():
    assert usage("/", statvfs=_fixed(blocks=0, bfree=0, bavail=0)).percent == 0


def test_the_real_root_reads_a_sane_percentage():
    u = usage("/")
    assert 0 <= u.percent <= 100
    assert u.avail_bytes > 0


# --- consumers -----------------------------------------------------------------


def test_consumers_report_each_known_path_largest_first(tmp_path: Path):
    big = tmp_path / "big"
    small = tmp_path / "small"
    (big / "nested").mkdir(parents=True)
    small.mkdir()
    (big / "nested" / "a").write_bytes(b"x" * 5000)
    (small / "b").write_bytes(b"x" * 10)
    missing = tmp_path / "missing"

    sizes = consumers([small, missing, big])
    assert [p for p, _ in sizes] == [big, small]
    assert sizes[0][1] >= 5000


def test_consumers_do_not_follow_symlinks_out_of_the_tree(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge").write_bytes(b"x" * 100_000)
    tree = tmp_path / "tree"
    tree.mkdir()
    os.symlink(outside, tree / "link")

    [(_, size)] = consumers([tree])
    assert size < 100_000


# --- main ----------------------------------------------------------------------


def _usage(percent: int):
    return lambda _path: Usage(used_bytes=percent, avail_bytes=100 - percent, percent=percent)


def test_below_the_threshold_exits_zero(caplog):
    caplog.set_level("INFO")
    assert main(["--alert-at", "85"], usage_fn=_usage(84), consumers_fn=lambda _: []) == 0
    assert "84%" in caplog.text


def test_at_the_threshold_exits_three(caplog):
    assert main(["--alert-at", "85"], usage_fn=_usage(85), consumers_fn=lambda _: []) == 3


def test_an_alert_names_the_largest_consumers(caplog):
    caplog.set_level("INFO")
    sizes = [(Path("/home/exedev/.npm"), 2 * 1024**3)]
    assert main(["--alert-at", "50"], usage_fn=_usage(90), consumers_fn=lambda _: sizes) == 3
    assert "/home/exedev/.npm" in caplog.text
    assert "2.0G" in caplog.text


def test_a_green_run_does_not_walk_the_consumers():
    def boom(_paths):
        raise AssertionError("walked the tree on a green run")

    assert main([], usage_fn=_usage(10), consumers_fn=boom) == 0


def test_the_default_threshold_is_eighty_five():
    assert DEFAULT_ALERT_AT == 85


def test_a_test_threshold_fires_on_the_real_disk():
    """The host acceptance check, in miniature: threshold 1 fires on any real root."""
    assert main(["--alert-at", "1"], consumers_fn=lambda _: []) == 3


@pytest.mark.parametrize("bad", ["0", "101", "-5", "x"])
def test_an_out_of_range_threshold_is_a_usage_error(bad):
    with pytest.raises(SystemExit) as exc:
        main(["--alert-at", bad])
    assert exc.value.code == 2
