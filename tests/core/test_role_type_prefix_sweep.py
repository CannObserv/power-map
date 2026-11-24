"""Every seeded non-jurisdictional role type carries a domain prefix (#266 rule 2, #303).

`role_types` is a flat, global aggregation key. Rule 2 of the governance in
`docs/SCHEMA_INDEXES.md` keeps it from becoming a domain-ambiguous grab-bag: a
non-jurisdictional slug is prefixed by the org-kind it attaches to, so a bare
noun such as `member` or `chair` can never mean two things at once. This sweep
makes the rule executable over the seed in `src/core/schema.sql`; a new prefix
is a governance change and is added here deliberately, with the doc.

Jurisdictional seat types (`expects_jurisdiction = TRUE`) are unprefixed by the
rule's scope, not by exemption.
"""

import re
from pathlib import Path

SCHEMA = Path("src/core/schema.sql").read_text()

# The org-kind prefixes rule 2 recognises. `org_` is the generic organization
# domain (#303): ownership, governance, management, membership, representation.
DOMAIN_PREFIXES = ("committee_", "chamber_", "legislature_", "party_", "org_")

_SEED_BLOCK_RE = re.compile(
    r"INSERT INTO _seed_role_types \(id, slug, display_name, expects_jurisdiction,"
    r"[^)]*\) VALUES(.*?);",
    re.DOTALL,
)
_ROW_RE = re.compile(r"\(\s*'(\w+)',\s*'(\w+)',\s*'[^']*',\s*(TRUE|FALSE)\s*,")


def _seeded_role_types(sql: str = SCHEMA) -> dict[str, bool]:
    """Map each seeded slug to its expects_jurisdiction flag."""
    block = _SEED_BLOCK_RE.search(sql)
    assert block, "role_types seed block not found in schema.sql"
    return {slug: flag == "TRUE" for _id, slug, flag in _ROW_RE.findall(block.group(1))}


def test_seed_parser_reads_the_whole_seed():
    """The parser sees every seeded row, so the sweep below can't pass vacuously."""
    seeded = _seeded_role_types()
    assert {"state_senator", "committee_member", "party_member"} <= set(seeded)
    assert seeded["state_senator"] is True
    assert seeded["committee_member"] is False


def test_non_jurisdictional_seeded_slugs_carry_a_domain_prefix():
    unprefixed = sorted(
        slug
        for slug, expects_jurisdiction in _seeded_role_types().items()
        if not expects_jurisdiction and not slug.startswith(DOMAIN_PREFIXES)
    )
    assert unprefixed == [], (
        f"non-jurisdictional role types without a domain prefix: {unprefixed}. "
        "Prefix the slug by the org-kind it attaches to (docs/SCHEMA_INDEXES.md, rule 2)."
    )


def test_sweep_flags_an_unprefixed_slug():
    """Mutation check: a bare noun seeded non-jurisdictional is caught."""
    sql = SCHEMA.replace(
        "('01KX000000000000000000000C', 'party_member',",
        "('01KX000000000000000000000C', 'member',",
    )
    seeded = _seeded_role_types(sql)
    assert "member" in seeded and seeded["member"] is False
    assert not "member".startswith(DOMAIN_PREFIXES)
