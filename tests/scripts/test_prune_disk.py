"""Tests for scripts.prune_disk — the weekly root-disk prune (#665).

What it may remove grows without bound and nothing else removes it: a VS Code
server build per client update (~650M), a Claude Code extension version per
release (~255M, left behind while the server runs), and an npx install per
SocratiCode spec (~460M). The rule that keeps this from breaking a live
session is **anything a running process uses stays**: an exe, a cwd, a mapped
file or an open fd under the path. On top of that, each kind keeps what the
next session will reach for: the newest server by VS Code's own ``lru.json``,
the newest CLI, the registered extension version, and the npx install of the
repo's pinned ``SOCRATICODE_SPEC``.

Dry run by default (#402); ``--execute`` removes and runs the cache commands.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.prune_disk import (
    CACHE_COMMANDS,
    in_use,
    live_paths,
    main,
    npx_dir_name,
    npx_installs,
    obsolete_extensions,
    pinned_specs,
    vscode_clis,
    vscode_servers,
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
    return main(
        argv, home=_home(tmp_path), live_fn=set, run=runner, settings=tmp_path / "none.json"
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
        ["--execute"],
        home=tmp_path / "home",
        live_fn=set,
        run=missing,
        settings=tmp_path / "x.json",
    )
    assert rc == 1


def test_the_cache_commands_are_npm_clean_and_uv_prune():
    assert [list(c) for c in CACHE_COMMANDS] == [
        ["npm", "cache", "clean", "--force"],
        ["uv", "cache", "prune"],
    ]


def test_execute_never_removes_a_build_in_use(tmp_path: Path):
    """The live set reaches every kind's plan through main, not only the helpers."""
    home = _home(tmp_path)
    servers = home / ".vscode-server" / "cli" / "servers"
    live_now = {str(servers / SERVER_A / "server" / "node")}
    assert (
        main(["--execute"], home=home, live_fn=lambda: live_now, run=_Runner(), settings=home / "x")
        == 0
    )
    assert (servers / SERVER_A).exists()


@pytest.mark.parametrize("arg", ["--help"])
def test_help_exits_cleanly(arg, capsys):
    with pytest.raises(SystemExit) as exc:
        main([arg])
    assert exc.value.code == 0
    assert "Usage:" in capsys.readouterr().out
