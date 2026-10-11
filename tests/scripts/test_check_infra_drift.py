"""Infra drift guard (#560): the host's installed copies against `infra/`.

The comparison runs against a fake root under `tmp_path`, so these tests hold in
a worktree and in CI. The manifest tests read the tracked `infra/installed.toml`
and the tracked `docs/COMMANDS.md`: the manifest is only useful if it names every
file and agrees with the install lines an operator actually runs.
"""

import re
import sys
from pathlib import Path

import pytest

from scripts import check_infra_drift as drift

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"
COMMANDS_MD = ROOT / "docs" / "COMMANDS.md"
SYSTEMD = "/etc/systemd/system"

UNIT = """\
[Unit]
Description=example
# why it runs as exedev

[Service]
User=exedev
ExecStart=uv run python -m scripts.example
"""


# --- the tracked manifest ---------------------------------------------------


def test_every_infra_file_is_in_the_manifest():
    """A file the manifest omits is a file nothing ever compares."""
    manifest = drift.load_manifest(drift.MANIFEST)
    files = drift.repo_files(INFRA)

    assert files, "enumerated no infra files"
    assert sorted(manifest) == files


def test_the_enumeration_skips_terraform_and_the_manifest_itself():
    files = drift.repo_files(INFRA)

    assert not any(f.startswith("terraform/") for f in files)
    assert drift.MANIFEST.name not in files
    assert "system.slice.d/90-power-map-memory.conf" in files


def test_top_level_units_install_under_systemd_by_name():
    """COMMANDS.md installs every unit with `sudo cp infra/<unit> /etc/systemd/system/`."""
    manifest = drift.load_manifest(drift.MANIFEST)
    for rel, installed in manifest.items():
        if "/" not in rel:
            assert installed == Path(SYSTEMD) / rel, rel


def test_host_files_install_where_their_commands_line_puts_them():
    """The manifest and the `sudo install -t DIR` line must name the same place."""
    manifest = drift.load_manifest(drift.MANIFEST)
    install_line = re.compile(r"^sudo install .*-t (\S+) infra/(\S+)$", re.M)
    install_dirs = {m.group(2): m.group(1) for m in install_line.finditer(COMMANDS_MD.read_text())}
    host_files = [rel for rel in manifest if "/" in rel]

    assert host_files
    for rel in host_files:
        assert rel in install_dirs, f"{rel} has no `sudo install -t` line"
        assert manifest[rel] == Path(install_dirs[rel]) / Path(rel).name, rel


def test_installed_paths_are_distinct_and_absolute():
    manifest = drift.load_manifest(drift.MANIFEST)
    targets = list(manifest.values())

    assert all(t.is_absolute() for t in targets)
    assert len(set(targets)) == len(targets)


def test_a_relative_installed_path_is_refused(tmp_path: Path):
    bad = tmp_path / "installed.toml"
    bad.write_text('[installed]\n"x.service" = "etc/systemd/system/x.service"\n')

    with pytest.raises(drift.ManifestError, match="absolute"):
        drift.load_manifest(bad)


def test_a_manifest_without_the_table_is_refused(tmp_path: Path):
    bad = tmp_path / "installed.toml"
    bad.write_text('"x.service" = "/etc/systemd/system/x.service"\n')

    with pytest.raises(drift.ManifestError, match="installed"):
        drift.load_manifest(bad)


def test_the_guard_unit_counts_comment_only_drift_as_success():
    """Exit 4 is only a warning if the unit says so (as #632's parity unit does)."""
    lines = (INFRA / "power-map-infra-drift.service").read_text().splitlines()

    assert f"SuccessExitStatus={drift.EXIT_COMMENT_ONLY}" in lines
    assert "User=exedev" in lines
    assert "WorkingDirectory=/home/exedev/power-map" in lines
    assert "ExecStart=uv run python -m scripts.check_infra_drift" in lines


def test_the_guard_timer_is_daily_and_persistent():
    lines = (INFRA / "power-map-infra-drift.timer").read_text().splitlines()

    assert "OnCalendar=daily" in lines
    assert "Persistent=true" in lines


# --- directive normalisation ------------------------------------------------


def test_directives_drop_comments_blank_lines_and_leading_whitespace():
    raw = b"# head\n\n[Service]\n  # indented comment\n\tExecStart=x\n   \n"

    assert drift.directives(raw) == [b"[Service]", b"ExecStart=x"]


def test_a_semicolon_line_is_a_directive():
    """`;` is a comment to systemd, a statement to Perl (needrestart): err towards drift."""
    assert drift.directives(b"; note\n") == [b"; note"]


def test_inner_whitespace_is_significant():
    assert drift.directives(b"User = exedev\n") != drift.directives(b"User=exedev\n")


def test_trailing_whitespace_is_significant():
    """A space after a continuation backslash stops systemd joining the next line."""
    assert drift.directives(b"Description=a \\ \n") != drift.directives(b"Description=a \\\n")


def test_only_newlines_split_lines():
    """systemd reads `\\x0c` and friends as part of the line; `str.splitlines` would not."""
    assert drift.directives(b"User=exedev\x0c#root\n") == [b"User=exedev\x0c#root"]


def test_crlf_and_lf_files_have_the_same_directives():
    assert drift.directives(UNIT.encode().replace(b"\n", b"\r\n")) == drift.directives(
        UNIT.encode()
    )


# --- the comparison ---------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path):
    """A repo `infra/` and a fake root holding the installed copies."""
    infra = tmp_path / "infra"
    root = tmp_path / "root"
    infra.mkdir()
    (root / SYSTEMD.lstrip("/")).mkdir(parents=True)

    def install(rel: str, repo_text: str, installed_text: str | None = None) -> Path:
        target = Path(SYSTEMD) / rel if "/" not in rel else Path("/etc/sysctl.d") / Path(rel).name
        (infra / rel).parent.mkdir(parents=True, exist_ok=True)
        (infra / rel).write_text(repo_text)
        if installed_text is not None:
            on_disk = root / target.relative_to("/")
            on_disk.parent.mkdir(parents=True, exist_ok=True)
            on_disk.write_text(installed_text)
        manifest[rel] = target
        return target

    manifest: dict[str, Path] = {}

    def run() -> list[drift.Finding]:
        return drift.audit(infra, manifest, root=root)

    return install, run, root


def _kinds(findings: list[drift.Finding]) -> dict[str, str]:
    return {f.repo: f.kind for f in findings}


def test_identical_copies_are_clean(host):
    install, run, _ = host
    install("power-map-x.service", UNIT, UNIT)
    install("sysctl.d/90-x.conf", "vm.x = 1\n", "vm.x = 1\n")

    findings = run()

    assert findings == []
    assert drift.exit_code(findings) == 0


def test_a_comment_only_difference_is_a_warning(host):
    install, run, _ = host
    install("power-map-x.service", UNIT, UNIT.replace("why it runs", "stale reason"))

    findings = run()

    assert _kinds(findings) == {"power-map-x.service": drift.COMMENT}
    assert drift.exit_code(findings) == drift.EXIT_COMMENT_ONLY


def test_a_blank_line_difference_is_comment_only(host):
    install, run, _ = host
    install("power-map-x.service", UNIT, UNIT.replace("\n\n", "\n\n\n"))

    assert _kinds(run()) == {"power-map-x.service": drift.COMMENT}


def test_a_directive_difference_is_drift(host):
    install, run, _ = host
    install("power-map-x.service", UNIT, UNIT.replace("scripts.example", "scripts.old_example"))

    findings = run()

    assert _kinds(findings) == {"power-map-x.service": drift.DIRECTIVE}
    assert drift.exit_code(findings) == drift.EXIT_DRIFT


@pytest.mark.parametrize(
    ("repo", "installed"),
    [
        ("Description=a \\\nUser=exedev\n", "Description=a \\ \nUser=exedev\n"),
        ("User=exedev\n", "User=exedev\x0c#root\n"),
        ("Description=caf\xe9\n", "Description=caf\xe8\n"),
    ],
    ids=["space-after-backslash", "form-feed", "invalid-utf8"],
)
def test_parser_visible_differences_are_never_comment_only(host, repo, installed):
    """Each pair parses differently to systemd; a lossy normalisation called them comments."""
    install, run, root = host
    install("power-map-x.service", "")
    (root.parent / "infra" / "power-map-x.service").write_bytes(repo.encode("latin-1"))
    on_disk = root / SYSTEMD.lstrip("/") / "power-map-x.service"
    on_disk.write_bytes(installed.encode("latin-1"))

    assert _kinds(run()) == {"power-map-x.service": drift.DIRECTIVE}


def test_reordered_directives_are_drift(host):
    """Order matters to systemd (ExecStart= sequence, a reset `Key=` before a value)."""
    install, run, _ = host
    swapped = UNIT.replace("User=exedev\nExecStart=uv run python -m scripts.example", "")
    swapped += "ExecStart=uv run python -m scripts.example\nUser=exedev\n"
    install("power-map-x.service", UNIT, swapped)

    assert _kinds(run()) == {"power-map-x.service": drift.DIRECTIVE}


def test_the_directive_finding_carries_the_diff(host):
    install, run, _ = host
    install("power-map-x.service", UNIT, UNIT.replace("scripts.example", "scripts.old_example"))

    [finding] = run()

    assert "-ExecStart=uv run python -m scripts.old_example" in finding.detail
    assert "+ExecStart=uv run python -m scripts.example" in finding.detail
    assert "why it runs" not in finding.detail


def test_a_missing_installed_copy_is_drift(host):
    """A merged unit nobody installed is the #560 failure at its most complete."""
    install, run, _ = host
    install("power-map-x.service", UNIT)

    findings = run()

    assert _kinds(findings) == {"power-map-x.service": drift.MISSING}
    assert drift.exit_code(findings) == drift.EXIT_DRIFT


def test_an_unreadable_installed_copy_is_an_error(host, monkeypatch):
    install, run, root = host
    target = install("power-map-x.service", UNIT, UNIT)
    real_read = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self == root / target.relative_to("/"):
            raise PermissionError(13, "Permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    findings = run()

    assert _kinds(findings) == {"power-map-x.service": drift.UNREADABLE}
    assert drift.exit_code(findings) == drift.EXIT_ERROR


def test_an_installed_unit_with_no_repo_file_is_drift(host):
    """A unit retired from infra/ but still installed keeps running."""
    install, run, root = host
    install("power-map-x.service", UNIT, UNIT)
    (root / SYSTEMD.lstrip("/") / "power-map-retired.timer").write_text("[Timer]\n")

    findings = run()

    assert {f.installed.name: f.kind for f in findings} == {"power-map-retired.timer": drift.ORPHAN}
    assert drift.exit_code(findings) == drift.EXIT_DRIFT


def test_a_local_override_drop_in_is_drift(host):
    """`systemctl edit power-map` writes a drop-in that changes directives silently."""
    install, run, root = host
    install("power-map-x.service", UNIT, UNIT)
    dropin = root / SYSTEMD.lstrip("/") / "power-map-x.service.d" / "override.conf"
    dropin.parent.mkdir()
    dropin.write_text("[Service]\nUser=root\n")

    findings = run()

    assert [(f.kind, f.installed.name) for f in findings] == [(drift.ORPHAN, "override.conf")]


def test_units_of_other_services_are_not_orphans(host):
    install, run, root = host
    install("power-map-x.service", UNIT, UNIT)
    (root / SYSTEMD.lstrip("/") / "tailscaled.service").write_text("[Service]\n")

    assert run() == []


def test_directive_drift_outranks_an_unreadable_copy_and_comment_drift():
    findings = [
        drift.Finding(drift.COMMENT, "a", Path("/a")),
        drift.Finding(drift.UNREADABLE, "b", Path("/b")),
        drift.Finding(drift.DIRECTIVE, "c", Path("/c")),
    ]
    assert drift.exit_code(findings) == drift.EXIT_DRIFT
    assert drift.exit_code(findings[:2]) == drift.EXIT_ERROR


# --- the CLI ----------------------------------------------------------------


def _main(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["check_infra_drift", *argv])
    with pytest.raises(SystemExit) as exc:
        drift.main()
    return exc.value.code


def _cli_host(tmp_path: Path, installed_text: str) -> tuple[Path, Path]:
    infra = tmp_path / "infra"
    root = tmp_path / "root"
    (root / SYSTEMD.lstrip("/")).mkdir(parents=True)
    infra.mkdir()
    (infra / "power-map-x.service").write_text(UNIT)
    (root / SYSTEMD.lstrip("/") / "power-map-x.service").write_text(installed_text)
    (infra / "installed.toml").write_text(
        f'[installed]\n"power-map-x.service" = "{SYSTEMD}/power-map-x.service"\n'
    )
    return infra, root


def test_main_exits_zero_when_clean(tmp_path, monkeypatch, capsys):
    infra, root = _cli_host(tmp_path, UNIT)
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == 0
    assert "1 file" in out


def test_main_warns_on_comment_only_drift(tmp_path, monkeypatch, capsys):
    infra, root = _cli_host(tmp_path, UNIT.replace("why it runs", "stale"))
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == drift.EXIT_COMMENT_ONLY
    assert "comments only" in out
    assert "power-map-x.service" in out


def test_main_fails_on_directive_drift_and_names_the_remedy(tmp_path, monkeypatch, capsys):
    infra, root = _cli_host(tmp_path, UNIT.replace("exedev", "root"))
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == drift.EXIT_DRIFT
    assert "-User=root" in out
    assert "docs/COMMANDS.md" in out


def test_main_fails_when_infra_has_a_file_the_manifest_omits(tmp_path, monkeypatch, capsys):
    """The unit test guards the repo; the run guards a manifest edited on the host."""
    infra, root = _cli_host(tmp_path, UNIT)
    (infra / "power-map-y.timer").write_text("[Timer]\n")
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == drift.EXIT_ERROR
    assert "power-map-y.timer" in out


def test_main_fails_on_an_unparseable_manifest(tmp_path, monkeypatch, capsys):
    infra, root = _cli_host(tmp_path, UNIT)
    (infra / "installed.toml").write_text("[installed\n")
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == drift.EXIT_ERROR
    assert "manifest" in out


def test_an_unreadable_repo_file_names_the_repo_path(host, monkeypatch):
    install, run, _ = host
    install("power-map-x.service", UNIT, UNIT)
    real_read = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self.name == "power-map-x.service" and self.parent.name == "infra":
            raise PermissionError(13, "Permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    [finding] = run()

    assert finding.kind == drift.UNREADABLE
    assert "infra" in finding.detail
    assert drift.exit_code([finding]) == drift.EXIT_ERROR


def test_a_vanished_repo_file_is_unreadable_not_a_traceback(host):
    install, run, root = host
    install("power-map-x.service", UNIT, UNIT)
    (root.parent / "infra" / "power-map-x.service").unlink()

    [finding] = run()

    assert finding.kind == drift.UNREADABLE
    assert "infra" in finding.detail


def test_an_orphan_names_removal_not_reinstall(tmp_path, monkeypatch, capsys):
    """Re-installing cannot fix a unit the repo no longer has."""
    infra, root = _cli_host(tmp_path, UNIT)
    (root / SYSTEMD.lstrip("/") / "power-map-retired.timer").write_text("[Timer]\n")
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == drift.EXIT_DRIFT
    assert "re-install" not in out
    assert "sudo rm" in out


def test_an_enablement_link_in_a_wants_dir_is_not_an_orphan(host):
    """`systemctl add-wants power-map.service x` links a unit; it is not a power-map file."""
    install, run, root = host
    install("power-map-x.service", UNIT, UNIT)
    other = root / SYSTEMD.lstrip("/") / "other.service"
    other.write_text("[Service]\n")
    wants = root / SYSTEMD.lstrip("/") / "power-map-x.service.wants"
    wants.mkdir()
    (wants / "other.service").symlink_to(other)

    assert run() == []


TIMER = "[Timer]\nOnCalendar=daily\n\n[Install]\nWantedBy=timers.target\n"


def test_an_installed_but_never_enabled_timer_is_drift(host):
    """Copied without `enable --now`, a timer reads correctly and never fires."""
    install, run, _ = host
    install("power-map-x.timer", TIMER, TIMER)

    findings = run()

    assert _kinds(findings) == {"power-map-x.timer": drift.DISABLED}
    assert "timers.target.wants" in findings[0].detail
    assert drift.exit_code(findings) == drift.EXIT_DRIFT


def test_an_enabled_timer_is_clean(host):
    install, run, root = host
    target = install("power-map-x.timer", TIMER, TIMER)
    wants = root / SYSTEMD.lstrip("/") / "timers.target.wants"
    wants.mkdir()
    (wants / "power-map-x.timer").symlink_to(target)  # dangling under the fake root: lexists

    assert run() == []


def test_every_wanted_by_target_needs_its_link(host):
    install, run, root = host
    unit = "[Service]\nExecStart=x\n\n[Install]\nWantedBy=multi-user.target graphical.target\n"
    install("power-map-x.service", unit, unit)
    wants = root / SYSTEMD.lstrip("/") / "multi-user.target.wants"
    wants.mkdir()
    (wants / "power-map-x.service").symlink_to("/etc/systemd/system/power-map-x.service")

    [finding] = run()

    assert finding.kind == drift.DISABLED
    assert "graphical.target.wants" in finding.detail
    assert "multi-user" not in finding.detail


def test_a_missing_unit_is_not_also_reported_disabled(host):
    install, run, _ = host
    install("power-map-x.timer", TIMER)

    assert _kinds(run()) == {"power-map-x.timer": drift.MISSING}


def test_a_disabled_unit_names_enable(tmp_path, monkeypatch, capsys):
    infra, root = _cli_host(tmp_path, UNIT)
    (infra / "power-map-x.service").write_text(UNIT + "\n[Install]\nWantedBy=multi-user.target\n")
    (root / SYSTEMD.lstrip("/") / "power-map-x.service").write_bytes(
        (infra / "power-map-x.service").read_bytes()
    )
    code = _main(monkeypatch, "--infra", str(infra), "--root", str(root))
    out = capsys.readouterr().out

    assert code == drift.EXIT_DRIFT
    assert "systemctl enable" in out


def test_the_remedy_names_sections_commands_md_has():
    """The remedy defers to COMMANDS.md's blocks; a renamed heading would strand it."""
    headings = {
        ln.removeprefix("## ").strip()
        for ln in COMMANDS_MD.read_text().splitlines()
        if ln.startswith("## ")
    }
    named = re.findall(r"§ ([A-Z]\w*(?: \w+)*?)(?= or |;|$)", drift.REMEDY)

    assert named == ["Service Management", "Scheduled timers"]
    assert set(named) <= headings
