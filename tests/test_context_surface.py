"""Gates the *shape* of the agent-context surface — `AGENTS.md` and its doc index.

The budget tests elsewhere ask how big the surface is. These ask whether it is
built the way it has to be to stay that size, because two failures the size
check cannot see are what put the file over budget in the first place:

1. **Index lines accreting clauses (#484).** `AGENTS.md` was last measured under
   budget at `9ecae78`; the next reading was 6,045 of 6,000. `git log` puts the
   +69 on two commits that widened *existing* index blurbs — `1fc4841` (#459,
   `OBSERVATIONS.md`) and `ebc76a5` (#467, `MERGE.md`) — not on new policy. Each
   edit was locally reasonable and small, and nothing distinguished them from
   the one kind of index growth that must stay legal: a new doc getting a line.
   #428's "the index cannot grow" is unenforceable as written for exactly that
   reason, so the rule enforced here is the narrower one — *an index line is a
   pointer, bounded in length; a new doc may add a line, an existing line may
   not grow a clause.*

   A delta check against `HEAD` was the other candidate and was rejected: it
   passes the moment the growth is committed, so it guards a diff rather than a
   property, and it says nothing to whoever reads the file next.

2. **Bare counts (#483).** `AGENTS.md:60` claimed "180 `hx-get` reveals". Three
   parties measuring it got 180, 189 and 182, because the sentence never said
   what was being counted — so no reading could be checked against it. A count
   in a file loaded on every invocation costs tokens *and* rots, paying rent
   twice. `docs/CONTEXT.md` states the three forms a count may take; this file
   enforces the default one.

Both gates are scoped to `AGENTS.md` on purpose. The reference docs under
`docs/` are loaded on demand rather than on every invocation, and the count rule
alone has ~190 hits across that tree — a sweep of them is its own piece of work,
not a ratchet to switch on here. `docs/API_ENTITIES.md`'s routing table is the
one exception: its rows are pointers of the same kind, so they take the same
ceiling.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY = REPO_ROOT / "AGENTS.md"
DOCS = REPO_ROOT / "docs"

# Archival subtrees: written once, never loaded as context.
ARCHIVAL = {"plans", "research", "archive", "audits", "specs"}

# Whole-line ceiling for an index pointer, in characters. Set below the two
# blurbs that broke the budget (249 and 224) and above every line that survived
# the rewrite, so the ceiling is a property of the shape rather than a snapshot
# of the current longest line. A pointer that cannot be said in this much space
# is describing the doc's contents instead of naming what a task would need it
# for — which is what the doc's own opening paragraph is for.
INDEX_LINE_MAX = 200

# The routing table in API_ENTITIES.md: its "Covers" cell is the same artifact.
ROUTING_CELL_MAX = 100

# Cardinal words are the rot-prone form: they are always counts, unlike digits,
# which in this file are overwhelmingly ports, versions and issue numbers. "one"
# is excluded — it reads as "a single", not as a tally, and cannot drift the way
# "six" does.
CARDINALS = (
    "two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|twenty|dozen"
)
CARDINAL_RE = re.compile(rf"\b({CARDINALS})\b", re.IGNORECASE)

# The one digit shape worth gating: a number qualifying a backticked term, e.g.
# "180 `hx-get` reveals" — the claim that started #483. Bare digits are left
# alone because in this file they are overwhelmingly status codes, ports,
# standards and versions ("403 missing", "port 8001", "ISO 8601", "WCAG 2.1"),
# and a gate that cries wolf on those is a gate people delete. The gap is real
# and deliberate: "182 hx-get reveals", unbackticked, would pass. Cardinal words
# carry the rest, and docs/CONTEXT.md states the rule the gate approximates.
DIGIT_COUNT_RE = re.compile(r"\b\d{1,5} `")

# The escape hatch, and the reason the gate can be strict: a count may stay if
# the same line carries the command that re-derives it. Narrow on purpose —
# `wc -l` is what re-derivation looks like, whereas any backticked command would
# have let "Eight scheduled timers … `systemctl --failed`" through, and that
# command counts nothing.
REDERIVATION_RE = re.compile(r"`[^`]*wc -l[^`]*`")


def _lines(path: Path) -> list[tuple[int, str]]:
    return list(enumerate(path.read_text().splitlines(), start=1))


@pytest.fixture(scope="module")
def index_lines() -> list[tuple[int, str]]:
    """The `- [docs/X.md](…) — …` pointer lines of the Detail Docs index."""
    out, in_index = [], False
    for number, line in _lines(POLICY):
        if line.startswith("## "):
            in_index = line.startswith("## Detail Docs")
        elif in_index and line.startswith("- ["):
            out.append((number, line))
    return out


@pytest.fixture(scope="module")
def live_docs() -> set[Path]:
    """Every reference doc that is part of the loadable context surface."""
    return {p for p in DOCS.rglob("*.md") if not ARCHIVAL & set(p.relative_to(DOCS).parts[:-1])}


def test_the_index_is_not_empty(index_lines: list[tuple[int, str]]) -> None:
    """A parser that silently matches nothing would make every gate vacuous."""
    assert len(index_lines) > 10, (
        "found no Detail Docs index — the '## Detail Docs' heading or the "
        "'- [docs/…](…)' line shape changed, and every check below is now a no-op"
    )


def test_index_lines_stay_pointers(index_lines: list[tuple[int, str]]) -> None:
    """An existing index line may not accrete clauses (#484)."""
    too_long = [
        f"AGENTS.md:{number} is {len(line)} chars: {line[:80]}…"
        for number, line in index_lines
        if len(line) > INDEX_LINE_MAX
    ]
    assert not too_long, (
        f"index lines must stay under {INDEX_LINE_MAX} chars — a line says what a "
        "task would need the doc for, and the doc's own opening paragraph says "
        "what is in it. Adding a doc may add a line; an existing line growing a "
        "clause is what put this file over budget (#459, #467). Move the detail "
        "into the doc it points at.\n" + "\n".join(too_long)
    )


def test_every_indexed_path_resolves(index_lines: list[tuple[int, str]]) -> None:
    """A pointer at a doc that moved routes the reader nowhere."""
    missing = [
        f"AGENTS.md:{number} → {target}"
        for number, line in index_lines
        for target in re.findall(r"\]\((docs/[^)]+)\)", line)
        if not (REPO_ROOT / target).is_file()
    ]
    assert not missing, "index points at paths that do not exist:\n" + "\n".join(missing)


def test_every_live_doc_is_reachable_from_the_policy_file(live_docs: set[Path]) -> None:
    """No doc is loadable-but-unfindable.

    Reachability, not "indexed": routing docs (`SCHEMA.md`, `API_ENTITIES.md`,
    `ADMIN.md`) carry the pointers to their own sub-docs, which is exactly what
    keeps the top-level index one line per subject instead of one per file.
    """
    seen: set[Path] = set()
    frontier = [POLICY]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        for name in re.findall(r"(?:\]\(|`)((?:docs/)?[A-Za-z_0-9]+\.md)", current.read_text()):
            target = DOCS / Path(name).name
            if target.is_file():
                frontier.append(target)

    unreachable = sorted(str(p.relative_to(REPO_ROOT)) for p in live_docs - seen)
    assert not unreachable, (
        "these docs are loaded by nobody — link them from AGENTS.md's index, or "
        "from the doc that routes their subject:\n" + "\n".join(unreachable)
    )


def test_the_policy_file_carries_no_bare_counts() -> None:
    """A count either re-derives itself or drops its precision (#483)."""
    offenders = []
    for number, line in _lines(POLICY):
        if REDERIVATION_RE.search(line):
            continue
        for pattern in (CARDINAL_RE, DIGIT_COUNT_RE):
            for match in pattern.finditer(line):
                excerpt = line[max(0, match.start() - 40) : match.end() + 40]
                offenders.append(f"AGENTS.md:{number}: …{excerpt}…")
    assert not offenders, (
        "a count in this file rots silently and nothing detects it — three "
        "parties measured the same claim as 180, 189 and 182 (#483). Either drop "
        "the precision (the argument is almost never load-bearing on the number), "
        "attach the command that re-derives it in backticks on the same line, or "
        "make it a test. See docs/CONTEXT.md.\n" + "\n".join(offenders)
    )


def test_routing_table_cells_stay_pointers() -> None:
    """The same ceiling for the one doc-side index of the same shape."""
    routing = DOCS / "API_ENTITIES.md"
    too_long = []
    for number, line in _lines(routing):
        if not line.startswith("| ") or "](" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 3 and len(cells[2]) > ROUTING_CELL_MAX:
            too_long.append(f"API_ENTITIES.md:{number}: {cells[2]}")
    assert not too_long, (
        f"a routing row's 'Covers' cell stays under {ROUTING_CELL_MAX} chars — it "
        "names what the doc answers, it does not summarise it (#484):\n" + "\n".join(too_long)
    )
