"""The member-elect role types mirror their seats (#883, usa-wa#600).

usa-wa publishes a member-elect, a 2026 winner between the race call and the
term's first day, on a `seat-elect:` role of its own type. That office is
the seat's, held early: a House seat-elect is per-position like its seat, a
Senate seat-elect is one per district, and each holds one person at a time.
`role_types` has no remote write path (#302), so the seed is the only place the
vocabulary can change; this pins the two rows to their seats' flags in
`src/core/schema.sql`.
"""

import re
from pathlib import Path

SCHEMA = Path("src/core/schema.sql").read_text()

_SEED_BLOCK_RE = re.compile(
    r"INSERT INTO _seed_role_types \(id, slug, display_name, expects_jurisdiction,"
    r"[^)]*\) VALUES(.*?);",
    re.DOTALL,
)
_ROW_RE = re.compile(
    r"\(\s*'(\w+)',\s*'(\w+)',\s*'([^']*)',\s*(TRUE|FALSE),\s*(TRUE|FALSE),\s*(TRUE|FALSE),"
    r"\s*'(\w+)'\s*\)"
)


def _seed() -> dict[str, tuple]:
    """Each seeded slug → (display_name, expects_jurisdiction, requires_qualifier,
    forbids_qualifier, occupancy)."""
    block = _SEED_BLOCK_RE.search(SCHEMA)
    assert block, "role_types seed block not found in schema.sql"
    return {slug: tuple(rest) for _id, slug, *rest in _ROW_RE.findall(block.group(1))}


def test_the_parser_reads_every_seeded_row():
    """Every row in the block parses, so a mirror check cannot pass vacuously."""
    block = _SEED_BLOCK_RE.search(SCHEMA).group(1)
    assert len(_seed()) == block.count("('01KX")
    assert _seed()["state_senator"] == ("State Senator", "TRUE", "FALSE", "FALSE", "single")


def test_each_member_elect_type_mirrors_its_seat():
    seed = _seed()
    for seat in ("state_representative", "state_senator"):
        elect = f"{seat}_elect"
        assert elect in seed, f"{elect} is not seeded"
        assert seed[elect][1:] == seed[seat][1:], f"{elect}'s flags differ from {seat}'s"
        assert seed[elect][0] == f"{seed[seat][0]}-elect"
