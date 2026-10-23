"""Tests for scripts.check_disk_headroom — the root-disk headroom guard (#665).

The guard owns three things:

1. A percentage that matches ``df``'s ``Use%``: used over used-plus-available,
   rounded up, so root's reserved blocks never make the guard read lower than
   the number an operator sees.
2. Exit 3 at or above the threshold and nothing else; the unit's co-status
   check-in (#566) turns that into one alert and the next green run clears it.
3. A threshold low enough to fire on any disk, which is how the acceptance
   test ("fires on a test threshold") is run on the host.
4. Hysteresis (#690): once alerting, it stays alerting until ``Use%`` is at or
   below ``--clear-at`` (80). Between the two lines it keeps the previous
   state, read from a one-line file under systemd's ``$STATE_DIRECTORY``; a
   missing or unreadable file reads as ``ok``. A pre-commit test run moves the
   disk ~0.5G, enough to cross 85% both ways several times an hour.
5. Journal priority that matches the log level (#690): under systemd each line
   carries a ``<N>`` syslog prefix, so ``journalctl -p err`` finds an alert.
"""

import io
import logging
import os
from pathlib import Path

import pytest

from scripts.check_disk_headroom import (
    DEFAULT_ALERT_AT,
    DEFAULT_CLEAR_AT,
    STATE_FILE,
    Usage,
    consumers,
    log_handler,
    main,
    usage,
)


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


@pytest.fixture(autouse=True)
def _no_state_directory(monkeypatch):
    """Outside systemd there is no ``$STATE_DIRECTORY``; a test sets its own."""
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)


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


# --- hysteresis (#690) ---------------------------------------------------------


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch) -> Path:
    """A ``$STATE_DIRECTORY`` as systemd's ``StateDirectory=`` provides it."""
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    return tmp_path


def _run(percent: int, *argv: str) -> int:
    return main(list(argv), usage_fn=_usage(percent), consumers_fn=lambda _: [])


def test_the_default_clear_threshold_is_eighty():
    assert DEFAULT_CLEAR_AT == 80


def test_an_oscillating_reading_alerts_once_and_holds(state_dir: Path):
    """#690's acceptance: 84 → 85 → 84 → 85 is one alert and no ok."""
    assert [_run(p) for p in (84, 85, 84, 85, 81)] == [0, 3, 3, 3, 3]


def test_it_clears_at_or_below_the_clear_line(state_dir: Path):
    assert _run(90) == 3
    assert _run(81) == 3
    assert _run(80) == 0
    # Back in the band after clearing: still ok until 85.
    assert _run(84) == 0


def test_a_first_run_in_the_band_is_ok(state_dir: Path):
    assert _run(84) == 0


@pytest.mark.parametrize(("percent", "code"), [(79, 0), (80, 0), (85, 3), (99, 3)])
def test_a_first_run_outside_the_band_behaves_as_before(state_dir: Path, percent, code):
    assert _run(percent) == code


def test_without_a_state_directory_the_band_reads_ok():
    """A run by hand, outside the unit, keeps no state and reads the band as ok."""
    assert _run(90) == 3
    assert _run(84) == 0


@pytest.mark.parametrize("content", [b"", b"garbage\n", b"ALERT", b"\xff\xfealert"])
def test_an_unrecognised_state_file_reads_ok(state_dir: Path, content):
    (state_dir / STATE_FILE).write_bytes(content)
    assert _run(84) == 0


def test_a_non_utf8_state_file_does_not_crash_the_run(state_dir: Path):
    (state_dir / STATE_FILE).write_bytes(b"\xff\xfe\x00")
    assert _run(90) == 3


def test_an_unreadable_state_file_reads_ok(state_dir: Path):
    (state_dir / STATE_FILE).mkdir()  # reading a directory raises
    assert _run(84) == 0


def test_the_state_file_is_one_line(state_dir: Path):
    _run(90)
    assert (state_dir / STATE_FILE).read_text() == "alert\n"
    _run(70)
    assert (state_dir / STATE_FILE).read_text() == "ok\n"


def test_a_state_write_failure_is_logged_not_fatal(state_dir: Path, caplog):
    state_dir.chmod(0o500)
    try:
        assert _run(90) == 3
    finally:
        state_dir.chmod(0o700)
    assert "could not record state" in caplog.text


def test_only_the_first_of_several_state_directories_is_used(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("STATE_DIRECTORY", f"{first}:{second}")
    _run(90)
    assert (first / STATE_FILE).exists()
    assert not (second / STATE_FILE).exists()


def test_a_held_alert_says_so_and_names_the_consumers(state_dir: Path, caplog):
    caplog.set_level("INFO")
    _run(90)
    caplog.clear()
    sizes = [(Path("/home/exedev/.npm"), 2 * 1024**3)]
    assert main([], usage_fn=_usage(83), consumers_fn=lambda _: sizes) == 3
    assert "low headroom" in caplog.text
    assert "clears at 80%" in caplog.text
    assert "/home/exedev/.npm" in caplog.text


def test_a_clear_after_an_alert_is_logged(state_dir: Path, caplog):
    caplog.set_level("INFO")
    _run(90)
    caplog.clear()
    _run(75)
    assert "recovered" in caplog.text


def test_a_test_threshold_still_fires_with_the_default_clear_line(state_dir: Path):
    """``--alert-at 1`` sits below the default 80: the clear line follows it down."""
    assert _run(50, "--alert-at", "1") == 3


@pytest.mark.parametrize(("alert", "clear"), [("85", "85"), ("85", "90"), ("50", "60")])
def test_a_clear_line_at_or_above_the_alert_line_is_a_usage_error(alert, clear):
    with pytest.raises(SystemExit) as exc:
        main(["--alert-at", alert, "--clear-at", clear])
    assert exc.value.code == 2


@pytest.mark.parametrize("bad", ["-1", "100", "x"])
def test_an_out_of_range_clear_line_is_a_usage_error(bad):
    with pytest.raises(SystemExit) as exc:
        main(["--clear-at", bad])
    assert exc.value.code == 2


def test_a_custom_clear_line_is_honoured(state_dir: Path):
    assert _run(90, "--clear-at", "70") == 3
    assert _run(75, "--clear-at", "70") == 3
    assert _run(70, "--clear-at", "70") == 0


# --- journal priority (#690) ---------------------------------------------------


def _journal_env(stream) -> dict[str, str]:
    st = os.fstat(stream.fileno())
    return {"JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino}"}


@pytest.mark.parametrize(
    ("level", "prefix"),
    [(logging.ERROR, "<3>"), (logging.WARNING, "<4>"), (logging.INFO, "<6>")],
)
def test_on_the_journal_each_line_carries_its_syslog_priority(level, prefix, tmp_path: Path):
    with open(tmp_path / "stderr", "w") as stream:
        handler = log_handler(stream, _journal_env(stream))
        record = logging.LogRecord("x", level, __file__, 1, "low headroom: %s", ("/",), None)
        assert handler.format(record).startswith(prefix + logging.getLevelName(level))


def test_off_the_journal_lines_carry_no_prefix(tmp_path: Path):
    """A terminal, or a stream that is not the one systemd named, reads plain."""
    with open(tmp_path / "stderr", "w") as stream, open(tmp_path / "other", "w") as other:
        for env in ({}, _journal_env(other), {"JOURNAL_STREAM": "junk"}):
            handler = log_handler(stream, env)
            record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
            assert handler.format(record) == "ERROR x: boom", env


def test_a_stream_without_a_descriptor_carries_no_prefix():
    handler = log_handler(io.StringIO(), {"JOURNAL_STREAM": "1:2"})
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
    assert handler.format(record) == "ERROR x: boom"
