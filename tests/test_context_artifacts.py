"""Guards `.socraticodecontextartifacts.json`, the SocratiCode context-artifact manifest.

The manifest names the project's non-code knowledge (SQL schema, reference docs)
so `codebase_context_search` can reach it. Two failure modes it has actually hit:

1. **Shape.** The server requires a top-level object; a bare array is rejected
   outright, and a rejected manifest makes `codebase_status` omit the artifact
   line entirely — the repo indexes "successfully" with no context search at all.
2. **Drift.** The manifest listed individual files under `docs/`. When the docs
   tree was split by subject (#407, #428, #444) it went from 7 files to 32 and
   the manifest was never updated, leaving 29 of them unreachable. Nothing
   noticed, because an absent doc reads as "no results" rather than "not indexed".

The coverage test below is the ratchet for (2): it does not care *how* the
manifest reaches a doc, only that every `docs/*.md` is reachable — so pointing a
single entry at the `docs` directory satisfies it permanently, and adding a new
doc can never silently fall out of the index.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / ".socraticodecontextartifacts.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    """The parsed manifest."""
    return json.loads(MANIFEST_PATH.read_text())


@pytest.fixture(scope="module")
def artifacts(manifest: dict) -> list[dict]:
    """The manifest's artifact entries."""
    return manifest["artifacts"]


def test_manifest_exists() -> None:
    """The manifest is present at the repo root."""
    assert MANIFEST_PATH.is_file(), f"missing {MANIFEST_PATH.name}"


def test_manifest_is_a_top_level_object(manifest: dict) -> None:
    """The server rejects a bare top-level array outright."""
    assert isinstance(manifest, dict), (
        "manifest must be a top-level object; a bare array is rejected by the "
        'server, which then reports "artifacts 0/0" with no context search at all'
    )
    assert isinstance(manifest.get("artifacts"), list), "'artifacts' must be a list"


def test_every_entry_has_the_required_triple(artifacts: list[dict]) -> None:
    """Each artifact carries {name, path, description}."""
    for entry in artifacts:
        missing = {"name", "path", "description"} - entry.keys()
        assert not missing, f"{entry.get('name', entry)} missing {sorted(missing)}"


def test_entry_names_are_unique_case_insensitively(artifacts: list[dict]) -> None:
    """Duplicate names collide server-side."""
    lowered = [entry["name"].lower() for entry in artifacts]
    duplicates = {name for name in lowered if lowered.count(name) > 1}
    assert not duplicates, f"duplicate artifact names: {sorted(duplicates)}"


def test_no_entry_uses_a_glob(artifacts: list[dict]) -> None:
    """Paths are `stat()`ed literally; a glob never resolves."""
    for entry in artifacts:
        path = entry["path"]
        assert not any(ch in path for ch in "*?["), (
            f"{entry['name']}: globs do not work — the server stat()s the literal "
            f"value; name a file or a directory instead ({path})"
        )


def test_every_path_resolves(artifacts: list[dict]) -> None:
    """A non-resolving path is skipped silently and never reaches parity."""
    for entry in artifacts:
        resolved = (REPO_ROOT / entry["path"]).resolve()
        assert resolved.exists(), f"{entry['name']}: path does not resolve ({entry['path']})"


def test_every_reference_doc_is_covered(artifacts: list[dict]) -> None:
    """Every `docs/*.md` is reachable, by file or by an ancestor directory.

    The ratchet against #407-style drift: a doc added to the tree must not fall
    out of the semantic index unnoticed.
    """
    covered_paths = [(REPO_ROOT / entry["path"]).resolve() for entry in artifacts]

    def is_covered(doc: Path) -> bool:
        return any(doc == candidate or candidate in doc.parents for candidate in covered_paths)

    uncovered = sorted(
        doc.relative_to(REPO_ROOT).as_posix()
        for doc in (REPO_ROOT / "docs").glob("*.md")
        if not is_covered(doc.resolve())
    )
    assert not uncovered, (
        "reference docs not reachable by codebase_context_search: "
        f"{uncovered}. Point an artifact entry at the `docs` directory (it "
        "indexes recursively) rather than naming files one by one."
    )
