"""Name-type registry (#857): one reference entry per person and org name type.

Each type carries its meaning, when to use it and when not to, an example, and
how power-map treats it. The behaviour sets the rest of the code reads —
promotion, canonical, display ladder, merge identity, parts decomposition,
landing visibility — are derived here, so no type's behaviour is listed twice.

The values themselves stay in ``src.core.types`` (``PersonNameType`` /
``PERSON_NAME_TYPES``), mirrored by the schema ``CHECK``; ``tests/core/
test_name_types.py`` holds this registry to that order, and to each derived
set's value. The public catalog (``GET /api/v1/person-name-types``) and
``docs/NAME_TYPES.md`` both read from here.
"""

from dataclasses import dataclass
from typing import Literal

from src.core.types import PersonNameType

LandingVisibility = Literal["public", "legal_only"]


@dataclass(frozen=True)
class OrgNameTypeSpec:
    """Reference entry for one organization name type."""

    slug: str
    label: str
    meaning: str
    use_when: str
    not_for: str
    example: str


@dataclass(frozen=True)
class PersonNameTypeSpec(OrgNameTypeSpec):
    """Reference entry for one person name type, with its behaviour flags.

    - ``display_rank``: place on the display ladder (lower wins); ``None`` = never
      picked as the displayed name unasked.
    - ``auto_promote``: may become canonical with no hint (insert, heal).
    - ``never_canonical``: a canonical hint is ignored on the wire and refused in
      admin, with ``never_canonical_reason`` as the admin's wording.
    - ``landing_visibility``: what ``trg_deadname_visibility`` forces on insert.
    - ``renders_row``: a rendering of another row, linked by ``reading_of_id``.
    - ``merge_distinct``: merge collapses it only against the same type.
    - ``decomposable``: the parts normalizer suggests a decomposition for it
      (admin's Suggest). Parts can still be entered or sent for any type.
    - ``presentable``: a consumer may show it as a name the person goes by.
    """

    display_rank: int | None
    auto_promote: bool
    never_canonical: bool
    never_canonical_reason: str | None
    landing_visibility: LandingVisibility
    renders_row: bool
    merge_distinct: bool
    decomposable: bool
    presentable: bool


def _display(
    slug: str, rank: int, *, decomposable: bool = True, **prose: str
) -> PersonNameTypeSpec:
    """An ordinary display type: on the ladder, promotable, public."""
    return PersonNameTypeSpec(
        slug=slug,
        display_rank=rank,
        auto_promote=True,
        never_canonical=False,
        never_canonical_reason=None,
        landing_visibility="public",
        renders_row=False,
        merge_distinct=False,
        decomposable=decomposable,
        presentable=True,
        **prose,
    )


def _rendering(slug: str, **prose: str) -> PersonNameTypeSpec:
    """A machine or phonetic rendering of another row (``reading_of_id``)."""
    return PersonNameTypeSpec(
        slug=slug,
        display_rank=None,
        auto_promote=False,
        never_canonical=False,
        never_canonical_reason=None,
        landing_visibility="public",
        renders_row=True,
        merge_distinct=True,
        decomposable=False,
        presentable=True,
        **prose,
    )


# Order is PERSON_NAME_TYPES' order (the admin dropdown's render order).
PERSON_NAME_TYPE_SPECS: tuple[PersonNameTypeSpec, ...] = (
    _display(
        "legal",
        2,
        label="Legal",
        meaning="The name on government records.",
        use_when="A government document or an official roster gives it.",
        not_for="A name the person only goes by: use preferred or variant.",
        example="Patricia Ann Lee",
    ),
    _display(
        "preferred",
        1,
        label="Preferred",
        meaning="The name the person asks to be called publicly.",
        use_when="The person states it, or uses it consistently in their own public material.",
        not_for="A short form others use that the person has not adopted: use variant.",
        example="Pat Lee, for legal Patricia Ann Lee",
    ),
    _display(
        "alias",
        3,
        label="Alias",
        meaning="A different name for the same person, not a form of their own name.",
        use_when="A pen name, a handle, an assumed name.",
        not_for="A short form or spelling of their own name: use variant.",
        example="Mark Twain, for Samuel Clemens",
    ),
    _display(
        "former",
        7,
        label="Former",
        meaning="A name the person no longer uses, changed by marriage, divorce or choice.",
        use_when="Records still carry the old name.",
        not_for="A pre-transition name (use deadname) or a birth surname (use maiden).",
        example="Jane Smith, before becoming Jane Doe",
    ),
    _display(
        "initials",
        8,
        decomposable=False,
        label="Initials",
        meaning="An initialism the person is known by.",
        use_when="The initials themselves are used as a name in public.",
        not_for="An abbreviated given name such as J. Lee: use variant.",
        example="JFK",
    ),
    _display(
        "maiden",
        6,
        label="Maiden",
        meaning="The person's birth surname, before a marriage change.",
        use_when="Records from before the marriage carry it.",
        not_for="Any other earlier name: use former.",
        example="Jane Doe, née Smith",
    ),
    _display(
        "religious",
        5,
        label="Religious",
        meaning="A religious or monastic name.",
        use_when="The person took the name on entering a religious life.",
        not_for="A title or honorific alone: that belongs in the name's parts.",
        example="Sister Mary Joseph",
    ),
    _display(
        "stage",
        4,
        label="Stage",
        meaning="A performer's or artist's professional name.",
        use_when="The person performs or publishes art under it.",
        not_for="A pen name or an online handle: use alias.",
        example="Lady Gaga, for Stefani Germanotta",
    ),
    PersonNameTypeSpec(
        slug="deadname",
        label="Deadname",
        meaning="A pre-transition or pre-disclosure name.",
        use_when="Only to match records that still carry it. It lands legal_only.",
        not_for="A name changed for any other reason: use former.",
        example="(kept private; never shown)",
        display_rank=None,
        auto_promote=False,
        never_canonical=True,
        never_canonical_reason="it is recorded privately and is never displayed",
        landing_visibility="legal_only",
        renders_row=False,
        merge_distinct=True,
        decomposable=True,
        presentable=False,
    ),
    _rendering(
        "reading",
        label="Reading",
        meaning="A phonetic reading of another name row.",
        use_when="A pronunciation guide (e.g. kana) for a name in another script.",
        not_for="A Latin-script form of the name: use romanization.",
        example="やまだ たろう, for 山田太郎",
    ),
    _rendering(
        "romanization",
        label="Romanization",
        meaning="A Latin-script rendering of another name row (pinyin, romaji).",
        use_when="The source name is in a non-Latin script.",
        not_for="A translated or adopted Western name: use alias or preferred.",
        example="Yamada Tarō, for 山田太郎",
    ),
    _rendering(
        "mrz",
        label="MRZ",
        meaning="The ICAO 9303 machine-readable-zone form of another name row.",
        use_when="Matching travel-document data.",
        not_for="Display: it is a machine form.",
        example="GARCIA<LOPEZ<<JOSE",
    ),
    PersonNameTypeSpec(
        slug="variant",
        label="Variant",
        meaning=(
            "Another form of the person's own name that they legitimately use or are"
            " known by: a short form, a nickname, or a second spelling their own"
            " usage attests."
        ),
        use_when=(
            "Kip for Kristopher; Becky for Rebecca; Jodi where the person's own"
            " documents use both Jody and Jodi."
        ),
        not_for=(
            "A spelling only others produce, such as a transcript's guess or a typo:"
            " use misspelling. A name the person asks to be shown under: use preferred."
        ),
        example="Kip, for Kristopher",
        display_rank=None,
        auto_promote=False,
        never_canonical=False,
        never_canonical_reason=None,
        landing_visibility="public",
        renders_row=False,
        merge_distinct=False,
        decomposable=True,
        presentable=True,
    ),
    PersonNameTypeSpec(
        slug="misspelling",
        label="Misspelling",
        meaning=(
            "A spelling the person does not use, kept only so that search finds them"
            " by it. Search leads to the canonical spelling."
        ),
        use_when=(
            "A transcript's guess or a common third-party typo of a name the record"
            " holds, e.g. a spelling heard in speech and never seen written."
        ),
        not_for=(
            "A nickname or short form, however unusual: use variant. Never show it"
            " as an also-known-as."
        ),
        example="Caitlin Ryan, for Kaitlyn Ryan",
        display_rank=None,
        auto_promote=False,
        never_canonical=True,
        never_canonical_reason=(
            "it is a misspelling kept only so search finds this person, and is never displayed"
        ),
        landing_visibility="public",
        renders_row=False,
        merge_distinct=True,
        decomposable=False,
        presentable=False,
    ),
)

ORG_NAME_TYPE_SPECS: tuple[OrgNameTypeSpec, ...] = (
    OrgNameTypeSpec(
        slug="legal",
        label="Legal",
        meaning="The organization's registered legal name.",
        use_when="A registry, charter or statute gives it.",
        not_for="A trade name: use dba.",
        example="Example Holdings, LLC",
    ),
    OrgNameTypeSpec(
        slug="dba",
        label="DBA",
        meaning="A trade name the organization does business as.",
        use_when="It operates or registers under a name other than its legal one.",
        not_for="A name it no longer uses: use former.",
        example="Example Market, for Example Holdings, LLC",
    ),
    OrgNameTypeSpec(
        slug="former",
        label="Former",
        meaning="A name the organization previously used.",
        use_when="It was renamed and records still carry the old name.",
        not_for="A trade name still in use: use dba.",
        example="Example Co., before becoming Example Holdings, LLC",
    ),
)

_PERSON_SPECS_BY_SLUG: dict[str, PersonNameTypeSpec] = {s.slug: s for s in PERSON_NAME_TYPE_SPECS}


def person_name_type_spec(slug: PersonNameType | str) -> PersonNameTypeSpec:
    """Return the registry entry for *slug*; ``KeyError`` for an unknown type."""
    return _PERSON_SPECS_BY_SLUG[slug]


def _slugs(pred) -> frozenset[str]:
    return frozenset(s.slug for s in PERSON_NAME_TYPE_SPECS if pred(s))


# Derived behaviour sets. Read these, never re-list them.
NO_AUTO_CANONICAL_NAME_TYPES = _slugs(lambda s: not s.auto_promote)
NEVER_CANONICAL_NAME_TYPES = _slugs(lambda s: s.never_canonical)
MERGE_DISTINCT_NAME_TYPES = _slugs(lambda s: s.merge_distinct)
NON_DECOMPOSABLE_NAME_TYPES = _slugs(lambda s: not s.decomposable)
LEGAL_ONLY_LANDING_NAME_TYPES = _slugs(lambda s: s.landing_visibility == "legal_only")
PERSON_NAME_TYPE_PRIORITY: dict[str, int] = dict(
    sorted(
        ((s.slug, s.display_rank) for s in PERSON_NAME_TYPE_SPECS if s.display_rank is not None),
        key=lambda kv: kv[1],
    )
)
