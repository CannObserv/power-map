"""Source-level sweep: every self-loading htmx host names its own target and swap (#547).

htmx inherits ``hx-target`` and ``hx-swap`` from ancestors
(``htmx.config.disableInheritance`` is false in the vendored 2.0.8). An element
that fires its own GET on ``load`` and leaves either attribute to inheritance
swaps its response into whatever an ancestor names. #547: the role title form's
overlay-note host sat inside ``<form hx-target="#title-field">``, so the note it
loaded replaced the whole form — and an empty note (a role outside the
producer's scope) left nothing at all.

A partial cannot see the ancestors of the page that includes it, so the rule is
unconditional: a host that is safe where it sits today names its target anyway.
``hx-swap="none"`` swaps nothing, so such a host needs no target.

Inheritance also runs the other way. Whatever the host names, the fragment it
loads inherits — and an inherited ``hx-target="this"`` still means the host, so
a boosted ``<a>`` in a dup badge loaded the whole page into the badge. So the
host also disinherits what it names (``hx-disinherit="hx-target hx-swap"``).
Never ``*``: htmx then cuts every attribute at the host, ``hx-boost`` included.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "templates"

# Comments, statements and expressions: blanked before the scan, since prose in a
# comment is not a host and a ``>`` in ``{% if n > 0 %}`` would end the tag early.
_JINJA = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.DOTALL)
_OPEN_TAG = re.compile(r"<[a-zA-Z][\w-]*\s[^>]*>")
# The event name ends at whitespace or a ``[filter]``; ``loadMore`` is another event.
_LOAD_EVENT = re.compile(r"load(?![\w-])")


def _fires_on_load(trigger: str) -> bool:
    """Whether any comma-separated trigger spec is a ``load`` event (``load delay:0ms`` too)."""
    return any(_LOAD_EVENT.match(spec.strip()) for spec in trigger.split(","))


def _attr(tag: str, name: str) -> str | None:
    """The opening tag's ``name`` value, single- or double-quoted; None when absent.

    The leading whitespace keeps ``data-hx-swap`` from reading as ``hx-swap``.
    """
    match = re.search(rf"""\s{name}=(["'])(.*?)\1""", tag, re.DOTALL)
    return match.group(2) if match else None


def _undeclared(tag: str) -> list[str]:
    """The ``hx-target`` / ``hx-swap`` a host leaves to inheritance.

    ``hx-swap="none"`` swaps nothing, so that host needs no target.
    """
    needed = ("hx-swap",) if _attr(tag, "hx-swap") == "none" else ("hx-target", "hx-swap")
    return [attr for attr in needed if _attr(tag, attr) is None]


def _hosts_in(template: str) -> list[tuple[int, str]]:
    """``(line, opening tag)`` for every element in ``template`` whose trigger fires on load.

    Jinja is blanked line-for-line first (``_JINJA``), so line numbers still
    match the file. Attribute names and quotes survive, so
    ``hx-target="#row-{{ id }}"`` still reads as declared; a trigger has to be
    literal markup to be seen.
    """
    source = _JINJA.sub(lambda m: "\n" * m.group().count("\n"), template)
    hosts = []
    for tag in _OPEN_TAG.finditer(source):
        trigger = _attr(tag.group(), "hx-trigger")
        if trigger is not None and _fires_on_load(trigger):
            hosts.append((source.count("\n", 0, tag.start()) + 1, tag.group()))
    return hosts


def _self_loading_hosts() -> list[tuple[str, str]]:
    """``(template:line, opening tag)`` for every self-loading host under ``src/templates``."""
    return [
        (f"{path.relative_to(TEMPLATES)}:{line}", tag)
        for path in sorted(TEMPLATES.rglob("*.html"))
        for line, tag in _hosts_in(path.read_text())
    ]


def test_fires_on_load_reads_each_trigger_spec():
    """The predicate: any spec's event is ``load``; modifiers and later specs don't hide it."""
    assert _fires_on_load("load")
    assert _fires_on_load("load delay:0ms")
    assert _fires_on_load("load, refreshOverlay from:body")
    assert _fires_on_load("refreshDupBadge from:body, load")
    assert not _fires_on_load("input changed delay:200ms")
    assert not _fires_on_load("refreshOverlay from:body")
    assert not _fires_on_load("")
    assert _fires_on_load("load[window.ready]")
    assert not _fires_on_load("loadMore")
    assert not _fires_on_load("load-more from:body")


def test_attr_reads_either_quote_style():
    """A single-quoted attribute is read like a double-quoted one; an absent one is ``None``."""
    assert _attr('<div hx-trigger="load" hx-get="/x">', "hx-trigger") == "load"
    assert _attr("<div hx-trigger='load, refreshOverlay from:body'>", "hx-trigger") == (
        "load, refreshOverlay from:body"
    )
    assert _attr('<div hx-get="/x">', "hx-trigger") is None
    assert _attr('<div data-hx-swap="none">', "hx-swap") is None


def test_undeclared_exempts_only_a_swapless_host_from_a_target():
    """``hx-swap="none"`` in either quote style needs no target; nothing else is exempt."""
    assert _undeclared('<div hx-trigger="load">') == ["hx-target", "hx-swap"]
    assert _undeclared('<div hx-trigger="load" hx-swap="none">') == []
    assert _undeclared("<div hx-trigger='load' hx-swap='none'>") == []
    assert _undeclared('<div hx-trigger="load" hx-swap="innerHTML">') == ["hx-target"]


def test_hosts_in_reads_past_a_jinja_comparison_in_the_tag():
    """A ``>`` inside ``{% … %}`` or ``{{ … }}`` must not end the tag before its trigger."""
    template = (
        "<p>intro</p>\n"
        '<div {% if n > 0 %}class="wide"{% endif %}\n'
        '     data-n="{{ n > 1 }}" hx-trigger="load" hx-target="this">'
    )
    [(line, tag)] = _hosts_in(template)
    assert line == 2
    assert _attr(tag, "hx-target") == "this"


def test_self_loading_hosts_are_discovered():
    """Guard the guard: a pattern drift must not silently empty the sweep."""
    hosts = _self_loading_hosts()
    assert len(hosts) >= 20, f"expected the overlay and dup-badge hosts, got {hosts}"
    assert any("overlay-note-host" in tag for _, tag in hosts)
    assert any("overlay-slot-host" in tag for _, tag in hosts)


def test_self_loading_hosts_name_their_own_target_and_swap():
    """No self-loading host inherits ``hx-target`` or ``hx-swap`` from an ancestor (#547)."""
    offenders = []
    for where, tag in _self_loading_hosts():
        if missing := _undeclared(tag):
            offenders.append(f"{where} (no {', '.join(missing)})")
    assert not offenders, (
        'a load-triggered host must name its own hx-target (usually "this") and hx-swap, '
        "or it swaps into whatever an ancestor names (#547):\n  " + "\n  ".join(offenders)
    )


def test_self_loading_hosts_disinherit_what_they_name():
    """What a host names for its own load stops at the host, not its fragment (#547)."""
    offenders = []
    for where, tag in _self_loading_hosts():
        disinherited = (_attr(tag, "hx-disinherit") or "").split()
        named = [attr for attr in ("hx-target", "hx-swap") if _attr(tag, attr) is not None]
        leaked = [attr for attr in named if attr not in disinherited]
        if "*" in disinherited or leaked:
            offenders.append(f"{where} (disinherits {disinherited or 'nothing'}, names {named})")
    assert not offenders, (
        "a load-triggered host must list what it names in hx-disinherit, never *, or its "
        "fragment's links inherit the host's target (#547):\n  " + "\n  ".join(offenders)
    )
