"""Canonical WA legislative role-title synthesis (#267).

Single source of truth for the human-readable title of a WA legislative role
(one with a role type + jurisdiction + qualifier), shared by the seed generator
(``scripts/generate_wa_roles.py``) and the observation resolver
(``src.core.observation.resolve_role``). Centralizing the format here means PM
curates the title deterministically from the structural tuple
``(role_type, jurisdiction, qualifier)`` — an upstream observer can omit
``title`` entirely and never nudge PM's curated form.

Scope: WA legislative roles only. A role whose ``role_type`` slug is unknown, or
whose jurisdiction is not a ``usa-wa-ld-{N}`` legislative district, cannot be
synthesized — the functions return ``None`` and the caller must supply a title.
"""

import re

_LD_SLUG_RE = re.compile(r"^usa-wa-ld-(\d+)$")

# role_type slug -> title base. Reproduces the #263 seed titles exactly:
#   "Washington State Senator, LD-{n}"
#   "Washington State Representative, LD-{n}, Position {p}"
_WA_ROLE_TITLE_BASE = {
    "state_senator": "Washington State Senator",
    "state_representative": "Washington State Representative",
}


def ld_number_from_slug(slug: str | None) -> int | None:
    """Return the LD number from a ``usa-wa-ld-{N}`` slug, else ``None``."""
    m = _LD_SLUG_RE.match(slug or "")
    return int(m.group(1)) if m else None


def wa_legislative_role_title(
    role_type_slug: str, ld_number: int | None, qualifier: str | None
) -> str | None:
    """Render a WA legislative role title, or ``None`` if it can't be synthesized.

    ``None`` when the role_type slug is not a known WA legislative office or the
    LD number is missing. A falsy qualifier adds no suffix.
    """
    base = _WA_ROLE_TITLE_BASE.get(role_type_slug)
    if base is None or ld_number is None:
        return None
    title = f"{base}, LD-{ld_number}"
    if qualifier:
        title = f"{title}, {qualifier}"
    return title


def synthesize_role_title(
    role_type_slug: str, jurisdiction_slug: str | None, qualifier: str | None
) -> str | None:
    """Synthesize a role title from the role_type slug + jurisdiction slug.

    Convenience wrapper for callers holding the jurisdiction *slug* (e.g.
    ``resolve_role``); returns ``None`` when synthesis is not possible.
    """
    return wa_legislative_role_title(
        role_type_slug, ld_number_from_slug(jurisdiction_slug), qualifier
    )
