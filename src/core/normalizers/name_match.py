"""Name matching for the applier's create hint (#533).

The hint names the PM rows already carrying a create's asserted name, so a twin
reads as a twin rather than a gap. A literal comparison missed 4 of the 17
duplicate people in #501's triage — curly quotes, a middle initial, a nickname
standing in for the given name, a note baked into the name — and "no match"
reads as evidence for creating the row. This module compares on structure
instead, and names the tier each match rests on:

- **person** — ``exact`` (folded text equal) → ``given`` (surname plus a given
  name or quoted nickname equal) → ``nickname`` (related through the
  ``nicknames`` dictionary) → ``initial`` (an initial against a given name).
  Middle initials and suffixes may be *absent* on one side, never *different*:
  that is what keeps N. B. and N. P. Atkinson, two people, apart. A trailing
  parenthetical note is dropped before parsing, so it lands a match on
  ``given`` at best, never ``exact``. ``initial`` ranks last because it is the
  noisiest tier on PM's corpus: an old roster's ``A. A. Smith`` meets every
  modern A-named Smith, where a nickname pair is usually one person.
- **organization** — ``exact`` → ``legal_form`` (equal once cleanco strips the
  legal form and ``&`` reads ``and``). A parenthetical stays: ``Noble Farms
  (Tacoma)`` is often a different branch, not a different spelling.
- anything else — ``exact`` only.

The ceiling is deliberate. A hint that names a probable twin costs an operator
one look and silence costs a duplicate, so recall wins — but only inside the
middle-and-suffix guard above. Without it, a given name plus surname collides
42 groups on the producer's corpus; with it, the four person tiers match 102
pairs of distinct people among PM's 4,893 live ones, 66 of those pairs only on
an initial (measured 2026-09-24). Anything softer belongs to the duplicate audit, not a per-create
hint.

Known gaps, each matching at ``exact`` or not at all:

- a person who goes by the middle name behind a first initial — ``J. Edgar
  Hoover`` against ``Edgar Hoover``. Closing it loosens the person tiers, so it
  waits on a measurement, not a guess.
- a given name nameparser reads as a title — ``Prince Fielder``, ``Judge
  Smith`` parse with no given name, ``King Lear`` with no surname. Reading the
  title as a given name instead would make ``Rev. Smith`` an ``initial`` twin of
  ``R. Smith``.
"""

import functools
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from cleanco import basename
from nameparser import HumanName
from nicknames import NickNamer

Tier = Literal["exact", "given", "nickname", "initial", "legal_form"]

# Strongest first: a lookup reports each parent at the first tier that holds.
TIERS: tuple[Tier, ...] = ("exact", "given", "nickname", "initial", "legal_form")

_PUNCT = str.maketrans(
    {
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("‐‑‒–—", "-"),
    }
)
_TRAILING_NOTE = re.compile(r"\s*\([^()]*\)\s*$")
_SPACE = re.compile(r"\s+")


def fold(text: str) -> str:
    """The text with formatting differences removed and nothing else.

    Quotes and dashes straighten, diacritics drop, whitespace collapses, case
    folds. Parentheticals stay: one is often what tells two entities apart.
    """
    s = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return _SPACE.sub(" ", s).strip().casefold()


@dataclass(frozen=True)
class _Person:
    surname: str
    given: frozenset[str]  # the first name and any nickname
    middle: tuple[str, ...]
    suffix: str


def _bare(token: str) -> str:
    return token.replace(".", "").replace(",", "").strip().strip("\"'")


@functools.lru_cache(maxsize=16384)
def _person(folded: str) -> _Person:
    """The parts of a person name, a trailing note dropped — ``(2)``, a service note —
    while an inner parenthetical stays for nameparser to read as the nickname."""
    name = HumanName(_TRAILING_NOTE.sub("", folded))
    return _Person(
        surname=name.last,
        given=frozenset(t for t in (_bare(name.first), _bare(name.nickname)) if t),
        middle=tuple(_bare(m) for m in name.middle.split()),
        suffix=_bare(name.suffix),
    )


@functools.cache
def _namer() -> NickNamer:
    return NickNamer()


def _compatible(p: str, q: str) -> bool:
    """Equal, or one is an initial the other starts with."""
    return p == q or (len(p) == 1 and q.startswith(p)) or (len(q) == 1 and p.startswith(q))


def _nicknamed(p: str, q: str) -> bool:
    namer = _namer()
    return q in namer.nicknames_of(p) or p in namer.nicknames_of(q)


def _person_key(folded: str) -> str:
    return _person(folded).surname or folded


def _person_tier(a: str, b: str) -> Tier | None:
    if a == b:
        return "exact"
    x, y = _person(a), _person(b)
    if not x.surname or x.surname != y.surname:
        return None
    if x.suffix and y.suffix and x.suffix != y.suffix:
        return None
    if not all(_compatible(p, q) for p, q in zip(x.middle, y.middle)):
        return None
    if x.given & y.given:
        return "given"
    if any(_nicknamed(p, q) for p in x.given for q in y.given):
        return "nickname"
    if any(_compatible(p, q) for p in x.given for q in y.given):
        return "initial"
    return None


def _org_key(folded: str) -> str:
    """The name without its legal form — or the whole name when that is all it is,
    so ``LLC`` and ``Inc.`` do not agree on an empty base."""
    return basename(_SPACE.sub(" ", folded.replace("&", " and ")).strip()) or folded


def _org_tier(a: str, b: str) -> Tier | None:
    if a == b:
        return "exact"
    return "legal_form" if _org_key(a) == _org_key(b) else None


def _exact_key(folded: str) -> str:
    return folded


def _exact_tier(a: str, b: str) -> Tier | None:
    return "exact" if a == b else None


def person_match(a: str, b: str) -> Tier | None:
    """The tier at which two person names match, or None."""
    return _person_tier(fold(a), fold(b))


def org_match(a: str, b: str) -> Tier | None:
    """The tier at which two organization names match, or None."""
    return _org_tier(fold(a), fold(b))


# entity → (blocking key, tier) over folded text. Two names that match at any
# tier share a key, so a lookup compares only within one block.
_KINDS: dict[str, tuple[Callable[[str], str], Callable[[str, str], Tier | None]]] = {
    "person": (_person_key, _person_tier),
    "organization": (_org_key, _org_tier),
}


class NameIndex:
    """The names PM holds for one entity type, blocked for cheap lookup."""

    def __init__(self, entity: str) -> None:
        self._key, self._tier = _KINDS.get(entity, (_exact_key, _exact_tier))
        self._blocks: dict[str, list[tuple[str, str]]] = {}

    def add(self, value: str, parent: str) -> None:
        """Hold ``value`` as a name of ``parent``; an empty name is no name."""
        folded = fold(value)
        if not folded:
            return
        self._blocks.setdefault(self._key(folded), []).append((folded, parent))

    def lookup(self, value: str) -> dict[str, Tier]:
        """Each parent holding a name that matches ``value``, at its strongest tier."""
        folded = fold(value)
        found: dict[str, Tier] = {}
        if not folded:
            return found
        for other, parent in self._blocks.get(self._key(folded), []):
            tier = self._tier(folded, other)
            if tier is not None and (
                parent not in found or TIERS.index(tier) < TIERS.index(found[parent])
            ):
                found[parent] = tier
        return found
