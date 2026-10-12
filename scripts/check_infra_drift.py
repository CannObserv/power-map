"""Infra drift guard (#560): the host's installed copies against ``infra/``.

Wired to ``infra/power-map-infra-drift.timer`` (daily). The host never runs the
files in ``infra/``; it runs copies in ``/etc``, made by the install lines in
``docs/COMMANDS.md`` § Service Management. Merging and pulling a change to one
changes nothing until someone re-runs that line, and nothing noticed when that
was skipped: on 2026-09-24 two units had been stale for 15 and 46 days. A stale
copy fails the way #541 found ``MemoryLow=`` failing — it reads correctly and
does nothing.

``infra/installed.toml`` maps each repo file to its installed path; the host
drop-ins are not at ``/etc/systemd/system/<name>``, so the path cannot be
derived. The run compares each pair from the main checkout (the deployed
commit) and classifies:

- **directive drift** — the files differ once comment and blank lines are
  dropped: exit 3, with the diff of the directive lines in the journal;
- **missing** — nothing is installed at the path: exit 3;
- **orphan** — ``/etc/systemd/system/power-map*`` holds a unit or a drop-in
  (``systemctl edit``) that no repo file accounts for: exit 3;
- **disabled** — an installed unit's ``WantedBy=`` / ``RequiredBy=`` target
  lacks its ``.wants/`` / ``.requires/`` link (copied, never enabled): a
  timer that reads correctly and never fires. Exit 3;
- **comments only** — directives identical, comments or blank lines not: a
  WARNING and exit 4, which the unit's ``SuccessExitStatus=4`` counts as
  success. Harmless to run, but the host is no longer byte-identical to the
  repo; re-copy at the next convenient install.

A comment is a whole line whose first non-blank character is ``#``. ``;`` is a
comment to systemd and sysctl but a statement to Perl (the needrestart
drop-in), so it reads as a directive: the error is towards a false alarm, never
a missed change. Lines are compared as bytes, split only where systemd splits
them; leading blanks are ignored, trailing and inner ones are not.

Read-only and unprivileged: every installed copy here is world-readable.
Installing stays a deliberate human step (restarts and ``daemon-reload`` touch
production), so the remedy is named, never run.

Exit codes: 0 = identical; 3 = directive drift, missing, orphan or disabled
(outranks the rest); 1 = a copy could not be read, or the manifest is unreadable or does not
match ``infra/``; 4 = comments only.

Usage:
    uv run python -m scripts.check_infra_drift
    uv run python -m scripts.check_infra_drift --root /mnt/rescue
"""

import difflib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from scripts._dsn import build_parser
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

INFRA = Path(__file__).resolve().parents[1] / "infra"
MANIFEST = INFRA / "installed.toml"
# Deployed by infra/terraform, never copied onto this host.
NOT_INSTALLED = "terraform"
SYSTEMD = Path("/etc/systemd/system")
# Installed names this project owns: anything here the manifest omits is an orphan.
OWNED_GLOB = "power-map*"

EXIT_ERROR = 1
EXIT_DRIFT = 3
EXIT_COMMENT_ONLY = 4

DIRECTIVE = "directive"
MISSING = "missing"
ORPHAN = "orphan"
UNREADABLE = "unreadable"
COMMENT = "comment"
DISABLED = "disabled"
_DRIFT_KINDS = frozenset({DIRECTIVE, MISSING, ORPHAN, DISABLED})
# [Install] keys that `systemctl enable` turns into a link, and the link's directory.
_ENABLE_KEYS = {b"WantedBy": ".wants", b"RequiredBy": ".requires"}

# Diff lines shown per file before the rest is counted: a rewrite stays readable.
MAX_DIFF_LINES = 20
# Each file's block names its own follow-up (daemon-reload, sysctl --system, a
# restart, enable --now), so the remedy points there rather than guessing one.
REMEDY = (
    "re-run each file's install block, from the main checkout, in docs/COMMANDS.md "
    "§ Service Management or § Scheduled timers; it names the reload or restart it needs"
)
DISABLED_REMEDY = "disabled: sudo systemctl enable --now <unit>"
# Nothing in the repo to re-install: the copy goes, or its file comes back.
ORPHAN_REMEDY = (
    "orphan: disable it if it is a timer, sudo rm it, then daemon-reload — or "
    "restore its infra/ file and manifest entry if it was retired by mistake"
)


class ManifestError(Exception):
    """``installed.toml`` is unreadable, malformed, or disagrees with ``infra/``."""


@dataclass(frozen=True)
class Finding:
    """One installed path that is not what its repo file says it should be."""

    kind: str
    repo: str
    installed: Path
    detail: str = ""


def repo_files(infra: Path) -> list[str]:
    """Every file under ``infra/`` the host runs a copy of, relative and sorted.

    Terraform deploys elsewhere and the manifest describes the others, so both
    are left out.
    """
    manifest = infra / MANIFEST.name
    return sorted(
        f.relative_to(infra).as_posix()
        for f in infra.rglob("*")
        if f.is_file() and f != manifest and f.relative_to(infra).parts[0] != NOT_INSTALLED
    )


def load_manifest(path: Path) -> dict[str, Path]:
    """Read ``[installed]`` from the manifest: repo-relative path → absolute installed path."""
    try:
        table = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    installed = table.get("installed")
    if not isinstance(installed, dict) or not installed:
        raise ManifestError(f"manifest {path} has no [installed] table")
    manifest = {}
    for rel, target in installed.items():
        if not isinstance(target, str) or not Path(target).is_absolute():
            raise ManifestError(f"manifest {path}: {rel} must map to an absolute path")
        manifest[rel] = Path(target)
    return manifest


def check_manifest(infra: Path, manifest: dict[str, Path]) -> None:
    """Raise unless the manifest names exactly the files ``repo_files`` finds."""
    files = set(repo_files(infra))
    unlisted = sorted(files - set(manifest))
    stale = sorted(set(manifest) - files)
    if unlisted or stale:
        raise ManifestError(
            f"manifest does not match infra/: unlisted {unlisted or 'none'}, "
            f"no such file {stale or 'none'}"
        )


def directives(raw: bytes) -> list[bytes]:
    """The lines that configure something: comment and blank lines dropped.

    Bytes, not text, so nothing two parsers would tell apart compares equal:
    ``bytes.splitlines`` breaks only on ``\\n``, ``\\r\\n`` and ``\\r``, as systemd's
    line reader does (``str.splitlines`` also breaks on ``\\x0c`` and friends), and
    invalid UTF-8 is never folded into one replacement character. Only leading
    blanks go: trailing ones are kept, since a space after a continuation
    backslash stops systemd joining the next line.
    """
    lines = (ln.lstrip(b" \t") for ln in raw.splitlines())
    return [ln for ln in lines if ln.strip(b" \t") and not ln.startswith(b"#")]


def _on_disk(root: Path, installed: Path) -> Path:
    return root / installed.relative_to("/")


def _shown(lines: list[bytes]) -> list[str]:
    return [ln.decode("utf-8", "backslashreplace") for ln in lines]


def _directive_diff(installed_raw: bytes, repo_raw: bytes) -> str:
    diff = list(
        difflib.unified_diff(
            _shown(directives(installed_raw)),
            _shown(directives(repo_raw)),
            fromfile="installed",
            tofile="repo",
            n=0,
            lineterm="",
        )
    )
    shown = diff[:MAX_DIFF_LINES]
    if len(diff) > MAX_DIFF_LINES:
        shown.append(f"... (+{len(diff) - MAX_DIFF_LINES} more lines)")
    return "\n".join(shown)


def compare(infra: Path, rel: str, installed: Path, root: Path) -> Finding | None:
    """Classify one pair, or ``None`` when the installed copy is byte-identical."""
    try:
        repo_bytes = (infra / rel).read_bytes()
    except OSError as exc:
        return Finding(UNREADABLE, rel, installed, f"repo side, {infra / rel}: {exc}")
    try:
        installed_bytes = _on_disk(root, installed).read_bytes()
    except FileNotFoundError:
        return Finding(MISSING, rel, installed)
    except OSError as exc:
        return Finding(UNREADABLE, rel, installed, str(exc))
    if repo_bytes == installed_bytes:
        return None
    if directives(repo_bytes) == directives(installed_bytes):
        return Finding(COMMENT, rel, installed)
    return Finding(DIRECTIVE, rel, installed, _directive_diff(installed_bytes, repo_bytes))


def orphans(manifest: dict[str, Path], root: Path) -> list[Finding]:
    """Owned units and drop-ins under ``/etc/systemd/system`` that no repo file accounts for."""
    expected = {_on_disk(root, p) for p in manifest.values()}
    systemd = _on_disk(root, SYSTEMD)
    found = []
    for entry in sorted(systemd.glob(OWNED_GLOB)):
        if entry.is_dir() and not entry.name.endswith(".d"):
            continue  # .wants/ and .requires/ hold links to other units, not ours
        candidates = sorted(entry.rglob("*")) if entry.is_dir() else [entry]
        for path in candidates:
            if path.is_file() and path not in expected:
                found.append(Finding(ORPHAN, "", Path("/") / path.relative_to(root)))
    return found


def missing_links(repo_raw: bytes, installed: Path, root: Path) -> list[Path]:
    """The enablement links ``systemctl enable`` would make for this unit and has not.

    Only a unit installed straight into ``/etc/systemd/system`` is enabled there; a
    drop-in or a non-systemd file has no ``[Install]`` to honour.
    """
    if installed.parent != SYSTEMD:
        return []
    links = []
    for line in directives(repo_raw):
        key, sep, value = line.partition(b"=")
        suffix = _ENABLE_KEYS.get(key.strip(b" \t"))  # systemd strips blanks around `=`
        if sep and suffix:
            for target in value.decode("utf-8", "replace").split():
                links.append(SYSTEMD / f"{target}{suffix}" / installed.name)
    # lexists: the link points at an absolute /etc path, dangling under a --root.
    return [link for link in links if not os.path.lexists(_on_disk(root, link))]


def audit(infra: Path, manifest: dict[str, Path], root: Path = Path("/")) -> list[Finding]:
    """Every finding for this manifest, the orphans last."""
    findings = []
    for rel, installed in sorted(manifest.items()):
        finding = compare(infra, rel, installed, root)
        if finding is not None:
            findings.append(finding)
        if finding is None or finding.kind in {COMMENT, DIRECTIVE}:
            unlinked = missing_links((infra / rel).read_bytes(), installed, root)
            if unlinked:
                detail = ", ".join(str(link) for link in unlinked)
                findings.append(Finding(DISABLED, rel, installed, detail))
    return findings + orphans(manifest, root)


def exit_code(findings: list[Finding]) -> int:
    """Drift outranks an unreadable copy, which outranks comment-only drift."""
    kinds = {f.kind for f in findings}
    if kinds & _DRIFT_KINDS:
        return EXIT_DRIFT
    if UNREADABLE in kinds:
        return EXIT_ERROR
    if COMMENT in kinds:
        return EXIT_COMMENT_ONLY
    return 0


def _report(findings: list[Finding], checked: int) -> None:
    for f in findings:
        if f.kind == DIRECTIVE:
            logger.error("infra drift: %s → %s differs in directives:", f.repo, f.installed)
            # One record per line: the JSON formatter escapes newlines inside a message.
            for line in f.detail.splitlines():
                logger.error("infra drift:   %s", line)
        elif f.kind == MISSING:
            logger.error("infra drift: %s is not installed at %s", f.repo, f.installed)
        elif f.kind == ORPHAN:
            logger.error(
                "infra drift: %s is installed but no infra/ file accounts for it", f.installed
            )
        elif f.kind == DISABLED:
            logger.error("infra drift: %s is installed but not enabled: no %s", f.repo, f.detail)
        elif f.kind == UNREADABLE:
            logger.error("infra drift: cannot compare %s → %s: %s", f.repo, f.installed, f.detail)
        else:
            logger.warning("infra drift: %s → %s differs in comments only", f.repo, f.installed)
    kinds = {f.kind for f in findings}
    if kinds & {DIRECTIVE, MISSING, COMMENT}:
        logger.warning("infra drift: %s", REMEDY)
    if DISABLED in kinds:
        logger.warning("infra drift: %s", DISABLED_REMEDY)
    if ORPHAN in kinds:
        logger.warning("infra drift: %s", ORPHAN_REMEDY)
    noun = "file" if checked == 1 else "files"
    logger.info("infra drift: %d %s checked, %d finding(s)", checked, noun, len(findings))


def main() -> None:
    """CLI entry point; exits 0, 1, 3 or 4 as the module docstring says."""
    configure_logging()
    parser = build_parser(__doc__)
    parser.add_argument(
        "--infra", type=Path, default=INFRA, help="repo infra/ (default: beside this script)"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/"),
        help="prefix for every installed path, e.g. a mounted rescue image (default /)",
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.infra / MANIFEST.name)
        check_manifest(args.infra, manifest)
    except ManifestError as exc:
        logger.error("infra drift: %s", exc)
        sys.exit(EXIT_ERROR)

    findings = audit(args.infra, manifest, root=args.root)
    _report(findings, len(manifest))
    sys.exit(exit_code(findings))


if __name__ == "__main__":
    main()
