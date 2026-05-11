"""Suggest-only decomposer for `person_names.name` → `person_name_parts` shape.

Issue #135. Used by:

- `scripts/analyse_person_name_parts.py` to bucket existing rows for human triage.
- The admin name editor (future) to pre-fill the parts form for review.

This module **never persists**. It returns `PartsSuggestion` objects; callers
that want to write to the DB must go through
`src.api.admin.people_name_parts.upsert_or_delete_parts` after a human
confirms.

Strategy:
    script == 'Latn' → wraps `nameparser.HumanName`, which already ships
        battle-tested lists for honorifics (`TITLES`),
        suffixes (`SUFFIX_ACRONYMS` / `SUFFIX_NOT_ACRONYMS`), surname
        particles (`PREFIXES`), and joiners (`CONJUNCTIONS`).
    other scripts  → returns `confidence='skip'`. Today's dataset is 100%
        Latn (issue #135 audit). Hant/Hans/Cyrl support plugs in here when
        we have data — see e.g. the `chinese-names` package for Hant/Hans.

`name_type` ∈ {initials, mrz, reading, romanization} returns 'skip' — these
rows have no semantically meaningful decomposition.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from nameparser import HumanName

# A bare initial: optionally periodized single uppercase letter ('A', 'A.').
_INITIAL_RE = re.compile(r"^[A-Z]\.?$")

# Mirror person_name_parts.primary_identifier CHECK constraint.
PrimaryIdentifier = Literal["family", "given", "patronymic", "mononym"]
Confidence = Literal["trivial", "ambiguous", "skip"]

# name_types whose `name` value is not a free human-name string. The parts
# editor on these rows is meaningless (per docs/CONVENTIONS.md §"Storage
# rules" — `initials` is e.g. "JFK", `mrz` is "GARCIA<LOPEZ<<JOSE",
# `reading` / `romanization` are phonetic transcriptions of another row).
NON_DECOMPOSABLE_TYPES: frozenset[str] = frozenset(
    {"initials", "mrz", "reading", "romanization"}
)


@dataclass(frozen=True)
class PartsSuggestion:
    """Suggestion for a `person_name_parts` row, with confidence + reasons.

    `confidence`:
        - ``trivial`` — high-confidence decomposition; safe to bulk-apply
          subject to spot-check.
        - ``ambiguous`` — decomposition possible but needs human review.
          Free fields may be partially populated as a starting point.
        - ``skip`` — decomposition refused (unsupported script, non-text
          name_type, empty input). All free fields are empty.

    `reasons` carries machine-readable tags (e.g. ``"particle:van der"``,
    ``"multi-token-no-particle"``) for CSV triage filtering.
    """

    given_names: list[str] = field(default_factory=list)
    family_names: list[str] = field(default_factory=list)
    additional_names: list[str] = field(default_factory=list)
    honorific_prefix: str | None = None
    honorific_suffix: str | None = None
    primary_identifier: PrimaryIdentifier | None = None
    confidence: Confidence = "trivial"
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def skip(cls, reason: str) -> "PartsSuggestion":
        return cls(confidence="skip", reasons=[reason])


# Surname particles that nameparser recognizes via its PREFIXES list. Used
# only for emitting human-readable `reasons` tags during decomposition; the
# actual particle handling (gluing them to the surname compound) happens
# inside HumanName.
_PARTICLE_TOKENS: frozenset[str] = frozenset({
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "do",
    "dos", "du", "la", "le", "el", "al", "bin", "binte", "ibn", "ben",
    "ter", "ten", "af", "av",
})


def _split_middle(middle: str) -> list[str]:
    """Split nameparser's `.middle` ('R. R.', 'Mary Anne', 'A.') into a list.

    Whitespace-tokenize but preserve dotted initials as single elements.
    Empty input → empty list.
    """
    return [tok for tok in middle.split() if tok]


def _is_pure_ascii_letters(s: str) -> bool:
    """True iff every alphabetic character in *s* is ASCII (a–z, A–Z)."""
    return all((not c.isalpha()) or ("A" <= c <= "Z" or "a" <= c <= "z") for c in s)


def _is_latin_script(s: str) -> bool:
    """True iff every alphabetic character is in the Unicode 'LATIN' script.

    Catches diacritics (`í`, `ã`, `ñ`, `ü`, …) — their Unicode names start
    with ``LATIN``. Excludes CJK / Cyrillic / Hangul / Arabic / etc.

    An alphabetic char with no Unicode name lookup (rare) returns False
    conservatively.
    """
    for c in s:
        if not c.isalpha():
            continue
        try:
            if not unicodedata.name(c).startswith("LATIN"):
                return False
        except ValueError:
            return False
    return True


def _suggest_latn(name: str) -> PartsSuggestion:
    """Decompose a Latn-script name via nameparser, mapping to PartsSuggestion."""
    hn = HumanName(name)

    title = hn.title.strip() or None
    suffix = hn.suffix.strip() or None
    first = hn.first.strip()
    last = hn.last.strip()
    middle = hn.middle.strip()

    given = [first] if first else []
    family = [last] if last else []
    additional = _split_middle(middle)

    reasons: list[str] = []

    # Mononym detection — clean single-token name with no recognized title /
    # suffix / middle. nameparser puts a lone token in `.first`, leaving
    # `.last` empty.
    if first and not last and not middle and not title and not suffix:
        # A hyphenated solo token ("Smith-Jones") is suspicious — could be a
        # surname stripped of a given name. Flag for review.
        if "-" in first:
            return PartsSuggestion(
                given_names=[first],
                primary_identifier=None,
                confidence="ambiguous",
                reasons=["hyphenated-single-token"],
            )
        return PartsSuggestion(
            given_names=[first],
            primary_identifier="mononym",
            confidence="trivial",
            reasons=["mononym"],
        )

    if not first and not last:
        # nameparser couldn't extract anything useful (e.g. punctuation-only).
        return PartsSuggestion.skip("nameparser-empty-decomposition")

    # Surname-particle detection: nameparser has already glued `van der` to
    # the surname; we annotate the reason for the triage CSV.
    if last:
        last_tokens = [t.lower() for t in last.split()]
        particles = [t for t in last_tokens if t in _PARTICLE_TOKENS]
        if particles:
            reasons.append(f"particle:{' '.join(particles)}")

    # Ambiguity rules. A name with two-or-more *non-initial* tokens in the
    # middle slot is the canonical ambiguous case — nameparser will dump
    # everything-not-first-or-last into `.middle`, and without a particle /
    # honorific / comma anchoring the partition we don't know whether
    # those middle tokens belong to the given chain, an additional
    # surname, or a patronymic.
    #
    # Initials (`J.`, `R.`) are exempt: a chain of dotted single letters
    # like "J. R. R. Tolkien" is unambiguously a given/initial sequence.
    raw_tokens = [t for t in name.replace(",", " ").split() if t]
    middle_tokens = additional
    all_middle_initials = bool(middle_tokens) and all(
        _INITIAL_RE.match(tok) for tok in middle_tokens
    )
    has_anchor = bool(
        title
        or suffix
        or any(r.startswith("particle:") for r in reasons)
        or "," in name
    )

    confidence: Confidence = "trivial"
    if len(middle_tokens) >= 2 and not all_middle_initials:
        confidence = "ambiguous"
        reasons.append("multi-token-middle")
    elif (
        len(raw_tokens) >= 4
        and not has_anchor
        and middle_tokens
        and not all_middle_initials
    ):
        confidence = "ambiguous"
        reasons.append("multi-token-no-particle")

    pi: PrimaryIdentifier | None = "family" if family else "given" if given else None

    return PartsSuggestion(
        given_names=given,
        family_names=family,
        additional_names=additional,
        honorific_prefix=title,
        honorific_suffix=suffix,
        primary_identifier=pi,
        confidence=confidence,
        reasons=reasons,
    )


def suggest_parts(
    name: str,
    *,
    locale: str,
    script: str,
    name_type: str,
) -> PartsSuggestion:
    """Suggest a `person_name_parts` decomposition for a `person_names` row.

    Never persists — caller is responsible for human review and routing
    confirmed values through `upsert_or_delete_parts`.

    Args:
        name: The free `person_names.name` string.
        locale: BCP 47, e.g. ``en-US``. Reserved for future per-locale
            constant overlays (CLDR personNames data); currently unused.
        script: ISO 15924, e.g. ``Latn``. Anything other than ``Latn``
            returns ``confidence='skip'``.
        name_type: Mirrors `person_names.name_type` CHECK. Rows whose
            value is not a free human-name string (initials/mrz/reading/
            romanization) return ``confidence='skip'``.
    """
    if name_type in NON_DECOMPOSABLE_TYPES:
        return PartsSuggestion.skip(f"name_type={name_type}")

    stripped = name.strip()
    if not stripped:
        return PartsSuggestion.skip("empty-name")

    if script != "Latn":
        return PartsSuggestion.skip(f"unsupported-script:{script}")

    return _suggest_latn(stripped)


def suggest_locale_script(name: str) -> tuple[str | None, str | None]:
    """Phase-1 helper: suggest (locale, script) for a row with both NULL.

    Three buckets:

    - **Pure ASCII** (`Jane Doe`, `John A. Doe`) → ``("en-US", "Latn")``.
      Today's dataset is overwhelmingly pure-ASCII; en-US is the safe
      default and Latn is unambiguous.
    - **Latin with diacritics** (`João Castel-Branco`, `Jürgen Unützer`,
      `Pedro García`) → ``(None, "Latn")``. The script is unambiguously
      Latin so we backfill it, but the *locale* is a judgment call
      (es vs. pt vs. de vs. fr…) — leave it NULL for human review.
    - **Anything else** (CJK, Cyrillic, Hangul, Arabic, mixed scripts,
      empty input) → ``(None, None)``. Both columns escalated.
    """
    if not name:
        return (None, None)
    if _is_pure_ascii_letters(name):
        return ("en-US", "Latn")
    if _is_latin_script(name):
        return (None, "Latn")
    return (None, None)
