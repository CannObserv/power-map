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
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "templates"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_OPEN_TAG = re.compile(r"<[a-zA-Z][\w-]*\s[^>]*>")
_TRIGGER = re.compile(r"""\shx-trigger=(["'])(.*?)\1""", re.DOTALL)
# The event name ends at whitespace or a ``[filter]``; ``loadMore`` is another event.
_LOAD_EVENT = re.compile(r"load(?![\w-])")


def _fires_on_load(trigger: str) -> bool:
    """Whether any comma-separated trigger spec is a ``load`` event (``load delay:0ms`` too)."""
    return any(_LOAD_EVENT.match(spec.strip()) for spec in trigger.split(","))


def _trigger_value(tag: str) -> str | None:
    """The opening tag's ``hx-trigger`` value, single- or double-quoted; None when absent."""
    match = _TRIGGER.search(tag)
    return match.group(2) if match else None


def _self_loading_hosts() -> list[tuple[str, str]]:
    """``(template:line, opening tag)`` for every element whose ``hx-trigger`` fires on load.

    Jinja comments are blanked line-for-line first, so prose quoting a trigger
    is not a host and line numbers still match the file.
    """
    hosts = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = _JINJA_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), path.read_text())
        for tag in _OPEN_TAG.finditer(source):
            trigger = _trigger_value(tag.group())
            if trigger is not None and _fires_on_load(trigger):
                line = source.count("\n", 0, tag.start()) + 1
                hosts.append((f"{path.relative_to(TEMPLATES)}:{line}", tag.group()))
    return hosts


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


def test_trigger_value_reads_either_quote_style():
    """A single-quoted ``hx-trigger`` is read like a double-quoted one; none is ``None``."""
    assert _trigger_value('<div hx-trigger="load" hx-get="/x">') == "load"
    assert _trigger_value("<div hx-trigger='load, refreshOverlay from:body'>") == (
        "load, refreshOverlay from:body"
    )
    assert _trigger_value('<div hx-get="/x">') is None


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
        if 'hx-swap="none"' in tag:
            continue
        missing = [attr for attr in ("hx-target", "hx-swap") if not re.search(rf"\s{attr}=", tag)]
        if missing:
            offenders.append(f"{where} (no {', '.join(missing)})")
    assert not offenders, (
        'a load-triggered host must name its own hx-target (usually "this") and hx-swap, '
        "or it swaps into whatever an ancestor names (#547):\n  " + "\n  ".join(offenders)
    )
