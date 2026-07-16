"""Rendered-DOM accessibility checks for admin templates (GH #246).

Operates on **resolved HTML** (includes expanded, ids materialized) — the
authoritative complement to the static per-template lint in
``test_aria_labels.py``, closing its three #244 blind spots via real tree
ancestry and document-wide id resolution.

Deliberately narrow: this is NOT a full AccName implementation. It asserts the
same properties as the static lint (a programmatic accessible name exists;
id references resolve), just against the real DOM. The full ruleset (contrast,
roles, focus order) is axe-core's job in the browser tier (GH #300).
"""

from lxml import html as lxml_html
from lxml.etree import _Element

# Elements the HTML spec considers labelable — determines which descendant a
# wrapping <label> actually names. Broader than the set we *require* names on.
_LABELABLE_TAGS = frozenset(
    {"input", "select", "textarea", "button", "meter", "output", "progress"}
)

# Controls that must carry an accessible name (mirrors the static lint scope).
_CHECKED_TAGS = frozenset({"input", "select", "textarea"})


def is_full_document(html: str) -> bool:
    """True if ``html`` is a full page (doctype/<html>), not an HTMX fragment."""
    head = html.lstrip()[:100].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


def _parse(html: str) -> _Element:
    if is_full_document(html):
        return lxml_html.document_fromstring(html)
    return lxml_html.fragment_fromstring(html, create_parent="div")


def _tag_repr(el: _Element) -> str:
    attrs = " ".join(f'{k}="{v}"' for k, v in el.attrib.items())
    return f"<{el.tag} {attrs}>"[:140] if attrs else f"<{el.tag}>"[:140]


def _is_labelable(el: _Element) -> bool:
    if not isinstance(el.tag, str) or el.tag not in _LABELABLE_TAGS:
        return False
    # Hidden inputs are not labelable per the HTML spec.
    return not (el.tag == "input" and (el.get("type") or "").lower() == "hidden")


def _is_checked_control(el: _Element) -> bool:
    """True for an input/select/textarea that must carry an accessible name
    (hidden inputs excluded — they have no perceivable UI to name)."""
    if not isinstance(el.tag, str) or el.tag not in _CHECKED_TAGS:
        return False
    return not (el.tag == "input" and (el.get("type") or "").lower() == "hidden")


def count_controls(html: str) -> int:
    """Number of name-requiring controls (input/select/textarea, excl. hidden)
    in the rendered tree. Lets a caller track aggregate control coverage across
    many renders so a mass drop (a form that silently stopped rendering) is
    visible rather than trivially green."""
    return sum(1 for el in _parse(html).iter() if _is_checked_control(el))


def _labeled_control(label: _Element) -> _Element | None:
    """The control a wrapping <label> names: its **first** labelable descendant
    in tree order — unless a ``for`` attribute redirects the association."""
    if label.get("for") is not None:
        return None  # names the for-target, resolved separately by id
    for el in label.iter():
        if el is not label and _is_labelable(el):
            return el
    return None


def _named_by_wrapping_label(el: _Element) -> bool:
    """True if ``el`` is the labeled control of an ancestor <label>.

    Walks up from the control rather than collecting ``id(...)`` values in a
    label-side pass: lxml element proxies are only identity-stable while a
    Python reference keeps them alive, so a set of ``id()`` ints from an
    earlier traversal silently fails to match on large documents. Here both
    proxies are alive during the ``is`` comparison, which lxml guarantees
    resolves to the same object for the same underlying node."""
    return any(
        anc.get("for") is None and _labeled_control(anc) is el for anc in el.iterancestors("label")
    )


def controls_missing_accessible_name(html: str) -> list[str]:
    """Serialized open tags of input/select/textarea lacking a programmatic
    accessible name, resolved through real ancestry (placeholder is not a
    label; a wrapping <label> names only its first labelable descendant)."""
    root = _parse(html)
    label_for_ids = {lab.get("for") for lab in root.iter("label") if lab.get("for")}
    missing: list[str] = []
    for el in root.iter():
        if not _is_checked_control(el):
            continue
        named = (
            bool((el.get("aria-label") or "").strip())
            or bool(el.get("aria-labelledby"))  # ref validity → dangling_id_refs
            or (el.get("id") in label_for_ids if el.get("id") else False)
            or _named_by_wrapping_label(el)
        )
        if not named:
            missing.append(_tag_repr(el))
    return missing


def dangling_id_refs(html: str) -> list[str]:
    """id references (<label for>, aria-labelledby/-describedby tokens) that
    resolve to no element id. Only meaningful on **full documents** — an HTMX
    fragment may legitimately reference ids rendered by its parent page.

    Corollary: on a fragment, an ``aria-labelledby``/``for`` target is *trusted,
    not verified* — a typo'd reference in a partial passes both this check (which
    the caller skips for fragments) and the accessible-name check (which accepts
    ``aria-labelledby`` by presence). Verifying fragment-local references needs
    the assembled parent DOM, which is the browser tier's job (GH #300)."""
    root = _parse(html)
    ids = {el.get("id") for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    problems: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in ("aria-labelledby", "aria-describedby"):
            for token in (el.get(attr) or "").split():
                if token not in ids:
                    problems.append(f"{attr} -> {token!r} unresolved on {_tag_repr(el)}")
        if el.tag == "label":
            target = el.get("for")
            if target and target not in ids:
                problems.append(f"label for -> {target!r} unresolved on {_tag_repr(el)}")
    return problems
