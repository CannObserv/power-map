"""Tests for scripts.prune_disk — the daily root-disk prune (#665, #690).

What it may remove grows without bound and nothing else removes it: a VS Code
server build per client update (~650M), a Claude Code extension version per
release (~255M, left behind while the server runs), and an npx install per
SocratiCode spec (~460M). The rule that keeps this from breaking a live
session is **anything a running process uses stays**: an exe, a cwd, a mapped
file or an open fd under the path. On top of that, each kind keeps what the
next session will reach for: the newest server by VS Code's own ``lru.json``,
the newest CLI, the registered extension version, and the npx install of the
repo's pinned ``SOCRATICODE_SPEC``. And a path touched within the grace window
(60 min, #669) stays too: it may be an install still being written, which no
process names yet.

Dry run by default (#402); ``--execute`` removes and runs the cache commands.
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import pytest

from scripts import check_disk_headroom, prune_disk
from scripts.prune_disk import (
    CACHE_COMMANDS,
    GRACE_MINUTES,
    in_use,
    launch_specs,
    live_paths,
    main,
    npx_dir_name,
    npx_installs,
    obsolete_extensions,
    pinned_specs,
    split_recent,
    vscode_clis,
    vscode_servers,
    withhold_reason,
)

SERVER_A = "Stable-aaaa"
SERVER_B = "Stable-bbbb"
SERVER_C = "Stable-cccc"


def _servers(home: Path, names: list[str], lru: list[str] | None) -> Path:
    servers = home / ".vscode-server" / "cli" / "servers"
    for name in names:
        (servers / name / "server").mkdir(parents=True)
        (servers / name / "server" / "node").write_bytes(b"x" * 100)
    if lru is not None:
        (servers / "lru.json").write_text(json.dumps(lru))
    return servers


def _extensions(home: Path, dirs: list[str], registered: list[str], obsolete: list[str]) -> Path:
    ext = home / ".vscode-server" / "extensions"
    ext.mkdir(parents=True)
    for name in dirs:
        (ext / name).mkdir(parents=True)
        (ext / name / "package.json").write_text("{}")
    (ext / "extensions.json").write_text(
        json.dumps([{"identifier": {"id": "x"}, "relativeLocation": r} for r in registered])
    )
    (ext / ".obsolete").write_text(json.dumps(dict.fromkeys(obsolete, True)))
    return ext


# --- live_paths / in_use ---------------------------------------------------------


def _fake_proc(root: Path, pid: str, *, exe=None, cwd=None, fds=(), maps="") -> None:
    p = root / pid
    (p / "fd").mkdir(parents=True)
    if exe:
        os.symlink(exe, p / "exe")
    if cwd:
        os.symlink(cwd, p / "cwd")
    for i, target in enumerate(fds):
        os.symlink(target, p / "fd" / str(i))
    (p / "maps").write_text(maps)


def test_live_paths_reads_exe_cwd_fds_and_maps(tmp_path: Path):
    proc = tmp_path / "proc"
    _fake_proc(
        proc,
        "101",
        exe="/srv/a/node",
        cwd="/srv/b",
        fds=["/srv/c/log", "socket:[123]"],
        maps=(
            "7f00-7f01 r-xp 00000000 08:01 42   /srv/d/lib.so\n"
            "7f01-7f02 rw-p 00000000 00:00 0 \n"
            "7f02-7f03 r--p 00000000 08:01 43   /srv/e/file with space (deleted)\n"
            "7ffd-7ffe rw-p 00000000 00:00 0    [stack]\n"
        ),
    )
    (proc / "self").mkdir()  # non-numeric entries are skipped
    live = live_paths(proc)
    assert {"/srv/a/node", "/srv/b", "/srv/c/log", "/srv/d/lib.so"} <= live
    assert "/srv/e/file with space" in live
    assert not any(p.startswith(("socket:", "[")) for p in live)


def test_live_paths_skips_a_process_it_cannot_read(tmp_path: Path):
    proc = tmp_path / "proc"
    (proc / "202").mkdir(parents=True)  # no exe, cwd, fd or maps: a root process
    _fake_proc(proc, "303", exe="/srv/ok")
    assert live_paths(proc) == {"/srv/ok"}


def test_in_use_matches_the_path_and_anything_under_it():
    live = {"/home/x/.vscode-server/cli/servers/Stable-aaaa/server/node"}
    assert in_use(Path("/home/x/.vscode-server/cli/servers/Stable-aaaa"), live)
    assert not in_use(Path("/home/x/.vscode-server/cli/servers/Stable-aa"), live)
    assert in_use(Path("/srv/a"), {"/srv/a"})


# --- VS Code server builds -------------------------------------------------------


def test_servers_keep_the_running_build_and_the_newest_by_lru(tmp_path: Path):
    servers = _servers(tmp_path, [SERVER_A, SERVER_B, SERVER_C], lru=[SERVER_C, SERVER_B, SERVER_A])
    live = {str(servers / SERVER_A / "server" / "node")}
    assert vscode_servers(tmp_path / ".vscode-server", live) == [servers / SERVER_B]


def test_servers_skip_lru_entries_already_gone(tmp_path: Path):
    servers = _servers(tmp_path, [SERVER_A, SERVER_B], lru=["Stable-gone", SERVER_B, SERVER_A])
    assert vscode_servers(tmp_path / ".vscode-server", set()) == [servers / SERVER_A]


def test_servers_fall_back_to_mtime_without_an_lru(tmp_path: Path):
    servers = _servers(tmp_path, [SERVER_A, SERVER_B], lru=None)
    os.utime(servers / SERVER_A, (1, 1))
    assert vscode_servers(tmp_path / ".vscode-server", set()) == [servers / SERVER_A]


def test_no_vscode_server_means_nothing_to_prune(tmp_path: Path):
    assert vscode_servers(tmp_path / ".vscode-server", set()) == []
    assert vscode_clis(tmp_path / ".vscode-server", set()) == []
    assert obsolete_extensions(tmp_path / ".vscode-server", set()) == []


# --- code-* CLIs -----------------------------------------------------------------


def test_clis_keep_every_running_cli_and_the_newest(tmp_path: Path):
    vs = tmp_path / ".vscode-server"
    vs.mkdir()
    for i, name in enumerate(["code-old", "code-running", "code-mid", "code-new"]):
        (vs / name).write_bytes(b"x")
        os.utime(vs / name, (1000 + i, 1000 + i))
    os.utime(vs / "code-running", (1, 1))  # oldest, but running: 2026-09-25's 520fb30b
    live = {str(vs / "code-running")}
    assert sorted(vscode_clis(vs, live)) == [vs / "code-mid", vs / "code-old"]


def test_clis_ignore_the_cli_logs(tmp_path: Path):
    vs = tmp_path / ".vscode-server"
    vs.mkdir()
    (vs / "code-a").write_bytes(b"x")
    (vs / ".cli.a.log").write_text("log")
    assert vscode_clis(vs, set()) == []


# --- obsolete extensions ---------------------------------------------------------


def test_obsolete_extensions_go_unless_registered_or_in_use(tmp_path: Path):
    ext = _extensions(
        tmp_path,
        dirs=["cc-1-linux-x64", "cc-2-linux-x64", "cc-3-linux-x64", "cc-4", "other-1"],
        registered=["cc-4", "other-1"],
        obsolete=["cc-1-linux-x64", "cc-2-linux-x64", "cc-3-linux-x64", "cc-0-linux-x64"],
    )
    live = {str(ext / "cc-1-linux-x64" / "resources" / "native-binary" / "claude")}
    assert sorted(obsolete_extensions(tmp_path / ".vscode-server", live)) == [
        ext / "cc-2-linux-x64",
        ext / "cc-3-linux-x64",
    ]


def test_a_registered_version_listed_obsolete_stays(tmp_path: Path):
    _extensions(tmp_path, dirs=["cc-4"], registered=["cc-4"], obsolete=["cc-4"])
    assert obsolete_extensions(tmp_path / ".vscode-server", set()) == []


def test_an_unreadable_registry_removes_no_extension(tmp_path: Path):
    ext = _extensions(tmp_path, dirs=["cc-1"], registered=[], obsolete=["cc-1"])
    (ext / "extensions.json").write_text("{not json")
    assert obsolete_extensions(tmp_path / ".vscode-server", set()) == []


def test_an_obsolete_key_cannot_escape_the_extensions_dir(tmp_path: Path):
    (tmp_path / "victim").mkdir()
    _extensions(tmp_path, dirs=[], registered=[], obsolete=["../../victim", "/etc"])
    assert obsolete_extensions(tmp_path / ".vscode-server", set()) == []


# --- npx installs ----------------------------------------------------------------


def test_npx_dir_name_is_npms_hash_of_the_spec():
    # Measured on this host, 2026-10-16: the live session server's dir.
    assert npx_dir_name("socraticode@1.15.0") == "a92f8746981a545a"
    assert npx_dir_name("socraticode@latest") == "e467c9db50cb633b"


def test_npx_keeps_the_pinned_spec_and_anything_in_use(tmp_path: Path):
    npx = tmp_path / ".npm" / "_npx"
    pinned = npx_dir_name("socraticode@1.15.0")
    for name in [pinned, "39052607b6e520af", "e467c9db50cb633b", "1111111111111111"]:
        (npx / name / "node_modules").mkdir(parents=True)
    live = {str(npx / "1111111111111111" / "node_modules" / "x.node")}
    got = npx_installs(tmp_path / ".npm", live, {"socraticode@1.15.0"})
    assert sorted(got) == [npx / "39052607b6e520af", npx / "e467c9db50cb633b"]


def test_pinned_specs_reads_the_repo_settings(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"SOCRATICODE_SPEC": "socraticode@1.15.0"}}))
    assert pinned_specs(settings) == {"socraticode@1.15.0"}


def test_pinned_specs_tolerates_a_missing_or_bad_file(tmp_path: Path):
    assert pinned_specs(tmp_path / "missing.json") == set()
    bad = tmp_path / "bad.json"
    bad.write_text("[")
    assert pinned_specs(bad) == set()


def _machine_settings(home: Path, body: str) -> Path:
    path = home / ".vscode-server" / "data" / "Machine" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(body)
    return path


def test_launch_specs_reads_the_vscode_machine_setting(tmp_path: Path):
    """What reaches the session's launch is this setting, not the repo's (#559)."""
    env = [{"name": "OTHER", "value": "x"}, {"name": "SOCRATICODE_SPEC", "value": "socraticode@9"}]
    path = _machine_settings(tmp_path, json.dumps({"claudeCode.environmentVariables": env}))
    assert launch_specs(path) == {"socraticode@9"}


@pytest.mark.parametrize(
    "body",
    ["{", "[]", "{}", '{"claudeCode.environmentVariables": {"SOCRATICODE_SPEC": "x"}}'],
    ids=["unparseable", "not-an-object", "no-setting", "dict-form"],
)
def test_launch_specs_tolerates_a_malformed_machine_setting(tmp_path: Path, body: str):
    assert launch_specs(_machine_settings(tmp_path, body)) == set()


def test_launch_specs_tolerates_no_machine_setting(tmp_path: Path):
    assert launch_specs(tmp_path / "missing.json") == set()


def test_execute_keeps_the_launched_spec_when_the_repo_declares_another(tmp_path: Path):
    """Idle, with the two pins diverged: the install the next session launches stays."""
    home = _home(tmp_path)
    env = [{"name": "SOCRATICODE_SPEC", "value": "socraticode@2.0.0"}]
    _machine_settings(home, json.dumps({"claudeCode.environmentVariables": env}))
    launched = home / ".npm" / "_npx" / npx_dir_name("socraticode@2.0.0")
    launched.mkdir()
    declared = tmp_path / "settings.json"
    declared.write_text(json.dumps({"env": {"SOCRATICODE_SPEC": "socraticode@1.15.0"}}))
    argv = ["--execute", "--grace-minutes", "0"]
    assert main(argv, home=home, live_fn=set, run=_Runner(), settings=declared) == 0
    assert launched.exists()
    assert not (home / ".npm" / "_npx" / "dead000000000000").exists()


def test_the_repo_pins_a_socraticode_spec():
    """The keep rule only protects the live server while the repo states its spec."""
    repo_settings = Path(__file__).resolve().parents[2] / ".claude" / "settings.json"
    assert {s for s in pinned_specs(repo_settings) if s.startswith("socraticode@")}


# --- main ------------------------------------------------------------------------


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    _servers(home, [SERVER_A, SERVER_B], lru=[SERVER_B, SERVER_A])
    (home / ".npm" / "_npx" / "dead000000000000").mkdir(parents=True)
    return home


class _Runner:
    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail

    def __call__(self, cmd, **_kwargs):
        self.calls.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 1 if tuple(cmd) == self.fail else 0, "", "")


def _main(tmp_path: Path, argv: list[str], runner: _Runner) -> int:
    """``main`` over a fresh home, grace off: every tree here was made a moment ago."""
    return main(
        [*argv, "--grace-minutes", "0"],
        home=_home(tmp_path),
        live_fn=set,
        run=runner,
        settings=tmp_path / "none.json",
    )


def test_a_dry_run_removes_nothing_and_runs_nothing(tmp_path: Path, caplog):
    caplog.set_level("INFO")
    runner = _Runner()
    assert _main(tmp_path, [], runner) == 0
    servers = tmp_path / "home" / ".vscode-server" / "cli" / "servers"
    assert (servers / SERVER_A).exists()
    assert (tmp_path / "home" / ".npm" / "_npx" / "dead000000000000").exists()
    assert runner.calls == []
    assert "would remove" in caplog.text
    assert "dry run" in caplog.text


def test_execute_removes_the_plan_and_runs_the_cache_commands(tmp_path: Path):
    runner = _Runner()
    assert _main(tmp_path, ["--execute"], runner) == 0
    servers = tmp_path / "home" / ".vscode-server" / "cli" / "servers"
    assert not (servers / SERVER_A).exists()
    assert (servers / SERVER_B).exists()
    assert not (tmp_path / "home" / ".npm" / "_npx" / "dead000000000000").exists()
    assert runner.calls == [tuple(c) for c in CACHE_COMMANDS]


def test_a_failing_cache_command_fails_the_run_but_not_the_rest(tmp_path: Path):
    runner = _Runner(fail=tuple(CACHE_COMMANDS[0]))
    assert _main(tmp_path, ["--execute"], runner) == 1
    assert len(runner.calls) == len(CACHE_COMMANDS)
    assert not (tmp_path / "home" / ".vscode-server" / "cli" / "servers" / SERVER_A).exists()


def test_a_missing_cache_tool_fails_the_run(tmp_path: Path):
    def missing(cmd, **_kwargs):
        raise FileNotFoundError(cmd[0])

    _home(tmp_path)
    rc = main(
        ["--execute", "--grace-minutes", "0"],
        home=tmp_path / "home",
        live_fn=set,
        run=missing,
        settings=tmp_path / "x.json",
    )
    assert rc == 1


def test_the_cache_commands_are_npm_clean_and_uv_prune():
    """``uv cache prune`` without ``--force`` waits for every other uv process.

    Production runs ``uv run uvicorn`` for good, so it would wait the whole
    timeout every week and page (CR 1, measured against a scratch cache).
    """
    assert [list(c) for c in CACHE_COMMANDS] == [
        ["npm", "cache", "clean", "--force"],
        ["uv", "cache", "prune", "--force"],
    ]


def test_execute_never_removes_a_build_in_use(tmp_path: Path):
    """The live set reaches every kind's plan through main, not only the helpers."""
    home = _home(tmp_path)
    servers = home / ".vscode-server" / "cli" / "servers"
    live_now = {str(servers / SERVER_A / "server" / "node")}
    argv = ["--execute", "--grace-minutes", "0"]
    assert main(argv, home=home, live_fn=lambda: live_now, run=_Runner(), settings=home / "x") == 0
    assert (servers / SERVER_A).exists()


# --- the grace window (#669) -------------------------------------------------------
#
# Liveness misses a tree being written right now: nothing runs from it yet. So
# a path touched within the grace stays. "Touched" is max(mtime, ctime): VS Code
# restores archive mtimes on extraction (a CLI read mtime 09-30, ctime 10-04,
# the day it landed), and ctime can't be backdated.


def _tree(root: Path) -> Path:
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "a" / "b" / "c" / "node").write_bytes(b"x")
    return root


def _settled() -> float:
    """A cutoff after everything made so far: the whole tree reads as settled."""
    return time.time() + 60


def test_the_default_grace_is_disk_gcs_hour():
    """usa-wa's DISK_GC_GRACE_MINUTES (CannObserv/usa-wa#394)."""
    assert GRACE_MINUTES == 60


def test_a_settled_tree_is_not_withheld(tmp_path: Path):
    assert withhold_reason(_tree(tmp_path / "t"), _settled()) is None


def test_an_extracted_tree_with_archive_mtimes_is_recently_touched(tmp_path: Path):
    """Every mtime weeks old, as an extractor leaves it; ctime says it just landed."""
    root = _tree(tmp_path / "t")
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames + dirnames:
            os.utime(os.path.join(dirpath, name), (1, 1))
        os.utime(dirpath, (1, 1))
    assert withhold_reason(root, time.time() - 3600) == "recently touched"


def test_a_file_with_an_archive_mtime_is_recently_touched(tmp_path: Path):
    """A ``code-*`` CLI is a plain file: its own stat is what counts."""
    cli = tmp_path / "code-new"
    cli.write_bytes(b"x")
    os.utime(cli, (1, 1))
    assert withhold_reason(cli, time.time() - 3600) == "recently touched"


def test_a_change_deep_in_a_settled_tree_withholds_it(tmp_path: Path):
    root = _tree(tmp_path / "t")
    cutoff = _settled()
    os.utime(root / "a" / "b" / "c", (cutoff + 3600, cutoff + 3600))
    assert withhold_reason(root, cutoff) == "recently touched"


def test_an_unreadable_path_is_withheld(tmp_path: Path):
    assert withhold_reason(tmp_path / "missing", _settled()).startswith("unreadable")


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists a mode-000 dir anyway")
def test_an_unlistable_subdir_withholds_the_tree(tmp_path: Path):
    root = _tree(tmp_path / "t")
    locked = root / "a" / "b"
    locked.chmod(0)
    try:
        assert withhold_reason(root, _settled()).startswith("unreadable")
    finally:
        locked.chmod(0o755)


def test_a_tree_past_the_walk_bound_is_withheld(tmp_path: Path):
    """Four dirs against a bound of two: unchecked means kept, never removed."""
    reason = withhold_reason(_tree(tmp_path / "t"), _settled(), bound=2)
    assert reason is not None and "bound" in reason


def test_split_recent_keeps_order_and_names_each_reason(tmp_path: Path):
    old_a, fresh, old_b = (_tree(tmp_path / n) for n in ("old-a", "fresh", "old-b"))
    cutoff = _settled()
    os.utime(fresh / "a", (cutoff + 3600, cutoff + 3600))
    missing = tmp_path / "missing"
    remove, withheld = split_recent([old_a, fresh, missing, old_b], cutoff)
    assert remove == [old_a, old_b]
    assert [p for p, _ in withheld] == [fresh, missing]
    assert withheld[0][1] == "recently touched"
    assert withheld[1][1].startswith("unreadable")


def test_by_default_a_fresh_install_is_withheld_not_removed(tmp_path: Path, caplog):
    """The race itself: a tree landed a moment ago, nothing runs from it yet."""
    caplog.set_level("INFO")
    home = _home(tmp_path)
    runner = _Runner()
    rc = main(["--execute"], home=home, live_fn=set, run=runner, settings=tmp_path / "x.json")
    assert rc == 0
    fresh_npx = home / ".npm" / "_npx" / "dead000000000000"
    assert fresh_npx.exists()
    assert (home / ".vscode-server" / "cli" / "servers" / SERVER_A).exists()
    assert f"withheld: recently touched  {fresh_npx}" in caplog.text
    assert "2 withheld" in caplog.text
    assert runner.calls == [tuple(c) for c in CACHE_COMMANDS]


def test_a_dry_run_names_what_the_grace_withholds(tmp_path: Path, caplog):
    caplog.set_level("INFO")
    home = _home(tmp_path)
    assert main([], home=home, live_fn=set, run=_Runner(), settings=tmp_path / "x.json") == 0
    assert f"withheld: recently touched  {home / '.npm' / '_npx' / 'dead000000000000'}" in (
        caplog.text
    )
    assert "would remove 0.0B across 0 paths" in caplog.text


def _run_with_a_locked_npx(tmp_path: Path, argv: list[str], caplog) -> tuple[int, Path, _Runner]:
    """``main`` two hours on, over a home whose stale npx tree holds a dir it can't list."""
    caplog.set_level("INFO")
    home = _home(tmp_path)
    stuck = home / ".npm" / "_npx" / "dead000000000000"
    (stuck / "root-owned").mkdir()
    (stuck / "root-owned").chmod(0)
    runner = _Runner()
    try:
        rc = main(
            argv,
            home=home,
            live_fn=set,
            run=runner,
            settings=tmp_path / "x.json",
            clock=lambda: time.time() + 7200,
        )
    finally:
        (stuck / "root-owned").chmod(0o755)
    return rc, stuck, runner


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists a mode-000 dir anyway")
def test_execute_fails_the_run_when_a_stale_path_cannot_be_checked(tmp_path: Path, caplog):
    """Before the grace, rmtree's PermissionError exited 1 and paged; a withhold mustn't hush it."""
    rc, stuck, runner = _run_with_a_locked_npx(tmp_path, ["--execute"], caplog)
    assert rc == 1
    assert stuck.exists()
    assert f"withheld: unreadable (Permission denied)  {stuck}" in caplog.text
    assert runner.calls == [tuple(c) for c in CACHE_COMMANDS]


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists a mode-000 dir anyway")
def test_a_dry_run_reports_an_unchecked_path_without_failing(tmp_path: Path, caplog):
    rc, stuck, _ = _run_with_a_locked_npx(tmp_path, [], caplog)
    assert rc == 0
    assert f"withheld: unreadable (Permission denied)  {stuck}" in caplog.text


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_grace_minutes_rejects_anything_but_whole_minutes(value, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--grace-minutes", value])
    assert exc.value.code == 2
    assert "_minutes" not in capsys.readouterr().err


@pytest.mark.parametrize("arg", ["--help"])
def test_help_exits_cleanly(arg, capsys):
    with pytest.raises(SystemExit) as exc:
        main([arg])
    assert exc.value.code == 0
    assert "Usage:" in capsys.readouterr().out


# --- journal priority (#690) ---------------------------------------------------


@pytest.mark.parametrize("level", [logging.ERROR, logging.WARNING, logging.INFO])
@pytest.mark.parametrize("on_journal", [True, False])
def test_journal_lines_match_the_headroom_guard(level, on_journal, tmp_path: Path):
    """The helper is duplicated from check_disk_headroom.py (neither file imports
    from the repo); its tests there are the spec, and this holds the copy to it."""
    with open(tmp_path / "stderr", "w") as stream:
        st = os.fstat(stream.fileno())
        env = {"JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino}"} if on_journal else {}
        record = logging.LogRecord("x", level, __file__, 1, "msg %s", ("a",), None)
        ours = prune_disk.log_handler(stream, env).format(record)
        theirs = check_disk_headroom.log_handler(stream, env).format(record)
    assert ours == theirs
    assert ours.startswith("<") is on_journal
