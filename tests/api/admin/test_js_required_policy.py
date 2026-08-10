"""Source-level sweeps for the JS-required admin policy (#287).

#287 audited whether no-JS *browser* support is attainable for the admin and
concluded it is not: 180 ``hx-get`` reveals mean every inline add/edit form is
fetched into the DOM on demand, so a JS-disabled browser cannot reach the bulk
of the admin's data-entry surface no matter how the mutation controls are
marked up.  The recorded policy (docs/ADMIN.md) is therefore **JS required**,
with the public API as the programmatic/no-JS surface.

Two guards ratchet the decision so it does not silently drift back:

1. Danger Zone controls are uniformly bare HTMX buttons.  Three holdouts used
   ``<form method="POST" action=...>``, which bought a JS-disabled operator
   archive/unarchive on a page where nothing else works — a half-measure that
   re-creates exactly the "do we support this?" ambiguity #287 closed.
2. ``base.html`` ships a ``<noscript>`` banner, so a JS-disabled operator is
   told the admin needs JS instead of clicking inert buttons.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "templates" / "admin"

_DANGER_ZONE_HEADING = "<h2>Danger Zone</h2>"


def _danger_zone_templates() -> list[Path]:
    return sorted(p for p in TEMPLATES.rglob("*.html") if _DANGER_ZONE_HEADING in p.read_text())


def test_danger_zone_templates_are_discovered():
    """Guard the guard: a rename must not silently empty the sweep."""
    found = _danger_zone_templates()
    assert len(found) >= 5, f"expected every entity detail page to carry a Danger Zone, got {found}"


def test_danger_zone_controls_carry_no_form_fallback():
    """Danger Zone mutations are bare hx-post/hx-delete buttons — never a <form> (#287).

    A ``<form method="POST" action=...>`` here is a partial no-JS affordance on
    a page whose every other control requires JS.  Policy is HTMX-required; the
    303 fallback in the route serves non-HTMX *clients*, not browsers.
    """
    offenders = []
    for path in _danger_zone_templates():
        danger_zone = path.read_text().split(_DANGER_ZONE_HEADING, 1)[1]
        if "<form" in danger_zone:
            offenders.append(str(path.relative_to(TEMPLATES)))
    assert not offenders, (
        "Danger Zone sections must use bare HTMX buttons, not <form> (#287): "
        + ", ".join(offenders)
    )


def test_base_template_has_noscript_banner():
    """A JS-disabled operator gets told, rather than clicking inert controls (#287)."""
    base = (TEMPLATES / "base.html").read_text()
    match = re.search(r"<noscript>(.*?)</noscript>", base, re.DOTALL)
    assert match, "base.html must ship a <noscript> banner stating the admin requires JS (#287)"
    body = match.group(1)
    assert "JavaScript" in body, "the <noscript> banner must name JavaScript"
    assert "/api/" in body or "API" in body, (
        "the <noscript> banner must point at the public API as the programmatic surface"
    )
