"""The one question Jev is asked about an organization's kind (#877).

Both the production client (``src/core/jev_client.py``) and the gold-set
builder (``scripts/org_kind_gold.py``) build their requests from here, so the
gold set scores exactly what runs. One org per request: the state holds one
``org``, and the question is one Choice named ``kind`` over the seeded kinds
(``org_kinds.description`` is each criterion) plus ``none``.

What is sent: the org's display name, the full names of the public registries
that list it (non-internal identifier types; never the values), and up to
``ROLE_TITLES_CHARS`` of the titles of its active roles. Never a person's name.
Changing ``CONTEXT``, ``INSTRUCTIONS``, ``NONE_CRITERION`` or a kind's
description means re-scoring the gold set.

Design: ``docs/plans/2026-11-24-org-kind-suggestions-design.md`` (D4, D6).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import asyncpg

CONTEXT = (
    "One organization from a public-affairs database that maps people, organizations and the "
    "roles people hold in them. `org.name` is its name; `org.listed_in` names the public "
    "registries that list it; `org.role_titles` are titles of roles held there, if any are known."
)
QUESTION_NAME = "kind"
INSTRUCTIONS = (
    "What kind of organization is `org`? If more than one kind fits, choose the most specific."
)
NONE_KIND = "none"
NONE_CRITERION = (
    "Not an organization (for example a person's name, or text entered by mistake), "
    "or none of the other kinds fits"
)
ROLE_TITLES_CHARS = 300
_SEP = "; "


@dataclass(frozen=True)
class OrgState:
    """What Jev is told about one org."""

    name: str
    listed_in: tuple[str, ...]
    role_titles: tuple[str, ...]


def criteria(kinds: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Return the Choice criteria: each ``(slug, description)`` in order, then ``none``."""
    crit: dict[str, str] = {}
    for slug, description in kinds:
        if slug == NONE_KIND:
            raise ValueError(f"a kind may not be named {NONE_KIND!r}: it is the no-kind option")
        crit[slug] = description
    crit[NONE_KIND] = NONE_CRITERION
    return crit


def build_questions(crit: dict[str, str]) -> dict[str, dict]:
    """Return the request's ``questions``: one Choice named ``QUESTION_NAME``."""
    return {QUESTION_NAME: {"type": "choice", "instructions": INSTRUCTIONS, "criteria": crit}}


def cap_titles(titles: Sequence[str], limit: int = ROLE_TITLES_CHARS) -> str:
    """Join whole titles with ``; `` up to *limit* characters.

    Whitespace is collapsed, case-insensitive repeats dropped, and the input
    order kept (``load_states`` passes them alphabetically). A title that doesn't
    fit is skipped, never cut.
    """
    seen: set[str] = set()
    out = ""
    for raw in titles:
        title = " ".join(raw.split())
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        candidate = f"{out}{_SEP}{title}" if out else title
        if len(candidate) <= limit:
            out = candidate
    return out


def build_state(org: OrgState) -> dict:
    """Return the request's ``state`` for one org."""
    return {
        "context": CONTEXT,
        "org": {
            "name": org.name,
            "listed_in": list(org.listed_in),
            "role_titles": cap_titles(org.role_titles),
        },
    }


async def load_kinds(db: asyncpg.Connection) -> list[tuple[str, str]]:
    """Return ``(slug, description)`` for every org kind, in seed (id) order."""
    rows = await db.fetch("SELECT slug, description FROM org_kinds ORDER BY id")
    return [(r["slug"], r["description"]) for r in rows]


_NAMES_SQL = """
SELECT organization_id, display_name FROM v_org_display_names
WHERE organization_id = ANY($1::text[])
"""
_LISTED_SQL = """
SELECT DISTINCT i.entity_id, t.full_name
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE i.entity_id = ANY($1::text[])
  AND t.entity_type = 'organization' AND NOT t.is_internal
ORDER BY i.entity_id, t.full_name
"""
_TITLES_SQL = """
SELECT organization_id, title
FROM roles
WHERE organization_id = ANY($1::text[]) AND archived_at IS NULL
ORDER BY organization_id, lower(title), title
"""


async def load_states(db: asyncpg.Connection, org_ids: Sequence[str]) -> dict[str, OrgState]:
    """Return each org's :class:`OrgState` (an unnamed org gets ``name=""``)."""
    ids = list(org_ids)
    names = {r["organization_id"]: r["display_name"] for r in await db.fetch(_NAMES_SQL, ids)}
    listed: dict[str, list[str]] = defaultdict(list)
    for r in await db.fetch(_LISTED_SQL, ids):
        listed[r["entity_id"]].append(r["full_name"])
    titles: dict[str, list[str]] = defaultdict(list)
    for r in await db.fetch(_TITLES_SQL, ids):
        titles[r["organization_id"]].append(r["title"])
    return {
        oid: OrgState(
            name=names.get(oid) or "",
            listed_in=tuple(listed[oid]),
            role_titles=tuple(titles[oid]),
        )
        for oid in ids
    }
