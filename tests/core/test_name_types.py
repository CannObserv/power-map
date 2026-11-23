"""The name-type registry (#857): one spec per type, and the sets derived from it.

Every behaviour set that used to be hand-listed (promotion, canonical, display
ladder, merge identity, parts decomposition, landing visibility) is now read off
``src.core.name_types``. These tests pin each derived set to its pre-#857 value
plus ``misspelling`` where the design note decided it — so a registry edit can
add a type, but cannot silently change an existing type's behaviour.
"""

import re
from pathlib import Path
from typing import get_args

import pytest

from src.api.public.schemas import ObservationPersonName
from src.core import name_types, observation, person_merge
from src.core.name_types import (
    LEGAL_ONLY_LANDING_NAME_TYPES,
    MERGE_DISTINCT_NAME_TYPES,
    NEVER_CANONICAL_NAME_TYPES,
    NO_AUTO_CANONICAL_NAME_TYPES,
    NON_DECOMPOSABLE_NAME_TYPES,
    ORG_NAME_TYPE_SPECS,
    PERSON_NAME_TYPE_PRIORITY,
    PERSON_NAME_TYPE_SPECS,
    person_name_type_spec,
)
from src.core.normalizers import person_name
from src.core.types import ORG_NAME_TYPES, PERSON_NAME_TYPES


def test_person_registry_order_matches_the_tuple():
    assert tuple(s.slug for s in PERSON_NAME_TYPE_SPECS) == PERSON_NAME_TYPES


def test_org_registry_order_matches_the_tuple():
    assert tuple(s.slug for s in ORG_NAME_TYPE_SPECS) == ORG_NAME_TYPES


def test_misspelling_is_a_person_name_type():
    assert "misspelling" in PERSON_NAME_TYPES


def test_org_types_have_no_misspelling():
    assert "misspelling" not in ORG_NAME_TYPES


@pytest.mark.parametrize("spec", PERSON_NAME_TYPE_SPECS + ORG_NAME_TYPE_SPECS, ids=lambda s: s.slug)
def test_every_spec_carries_the_reference_prose(spec):
    for field in ("label", "meaning", "use_when", "not_for", "example"):
        assert getattr(spec, field).strip(), f"{spec.slug}.{field} is empty"


# --- derived sets: pre-#857 value, plus misspelling where decided -------------


def test_no_auto_canonical_set():
    assert NO_AUTO_CANONICAL_NAME_TYPES == frozenset(
        {"deadname", "mrz", "romanization", "reading", "variant", "misspelling"}
    )


def test_never_canonical_set():
    assert NEVER_CANONICAL_NAME_TYPES == frozenset({"deadname", "misspelling"})


def test_display_ladder_is_unchanged():
    assert PERSON_NAME_TYPE_PRIORITY == {
        "preferred": 1,
        "legal": 2,
        "alias": 3,
        "stage": 4,
        "religious": 5,
        "maiden": 6,
        "former": 7,
        "initials": 8,
    }


def test_merge_distinct_set():
    assert MERGE_DISTINCT_NAME_TYPES == frozenset(
        {"deadname", "mrz", "romanization", "reading", "misspelling"}
    )


def test_non_decomposable_set():
    assert NON_DECOMPOSABLE_NAME_TYPES == frozenset(
        {"initials", "mrz", "reading", "romanization", "misspelling"}
    )


def test_only_deadname_lands_legal_only():
    assert LEGAL_ONLY_LANDING_NAME_TYPES == frozenset({"deadname"})


# --- invariants between flags -------------------------------------------------


@pytest.mark.parametrize("spec", PERSON_NAME_TYPE_SPECS, ids=lambda s: s.slug)
def test_auto_promote_iff_on_the_ladder(spec):
    # The heal picks by the ladder; a promotable type off the ladder would sort
    # last by accident rather than by decision.
    assert spec.auto_promote == (spec.display_rank is not None)


@pytest.mark.parametrize("spec", PERSON_NAME_TYPE_SPECS, ids=lambda s: s.slug)
def test_never_canonical_types_carry_a_refusal_reason(spec):
    assert bool(spec.never_canonical_reason) == spec.never_canonical


@pytest.mark.parametrize("spec", PERSON_NAME_TYPE_SPECS, ids=lambda s: s.slug)
def test_never_canonical_types_are_never_auto_promoted(spec):
    if spec.never_canonical:
        assert not spec.auto_promote


def test_ladder_ranks_are_distinct():
    ranks = [s.display_rank for s in PERSON_NAME_TYPE_SPECS if s.display_rank is not None]
    assert len(ranks) == len(set(ranks))


# --- misspelling, as decided ----------------------------------------------------


def test_misspelling_lands_public_and_is_never_presented():
    spec = person_name_type_spec("misspelling")
    assert spec.landing_visibility == "public"
    assert spec.presentable is False
    assert spec.renders_row is False


def test_variant_stays_a_presentable_display_type():
    spec = person_name_type_spec("variant")
    assert spec.presentable is True
    assert spec.merge_distinct is False
    assert spec.never_canonical is False


def test_reading_family_renders_another_row():
    assert {s.slug for s in PERSON_NAME_TYPE_SPECS if s.renders_row} == {
        "reading",
        "romanization",
        "mrz",
    }


def test_unknown_slug_raises():
    with pytest.raises(KeyError):
        person_name_type_spec("nickname")


# --- the call sites read the registry, never their own copy -------------------


def test_observation_reads_the_registry():
    assert observation.NO_AUTO_CANONICAL_NAME_TYPES is name_types.NO_AUTO_CANONICAL_NAME_TYPES
    assert observation.NEVER_CANONICAL_NAME_TYPES is name_types.NEVER_CANONICAL_NAME_TYPES
    assert observation._PERSON_NAME_TYPE_PRIORITY is name_types.PERSON_NAME_TYPE_PRIORITY


def test_person_merge_reads_the_registry():
    assert person_merge._MERGE_DISTINCT_NAME_TYPES is name_types.MERGE_DISTINCT_NAME_TYPES


def test_parts_normalizer_reads_the_registry():
    assert person_name.NON_DECOMPOSABLE_TYPES is name_types.NON_DECOMPOSABLE_NAME_TYPES


# --- the schema holds the canonical rule the registry states ------------------

_SCHEMA = Path("src/core/schema.sql").read_text()


def test_schema_never_canonical_check_matches_the_registry():
    # chk_person_canonical_name_type and the demote ahead of it share one
    # predicate spelling, and both list exactly the registry's never-canonical
    # types; a type added to one and not the other fails here.
    lists = re.findall(r"NOT\s+is_canonical\s+OR\s+name_type\s+NOT\s+IN\s*\(([^)]*)\)", _SCHEMA)
    assert "chk_person_canonical_name_type" in _SCHEMA
    assert len(lists) == 2, f"expected the CHECK and its demote, found {len(lists)}"
    for values in lists:
        assert set(re.findall(r"'([a-z_]+)'", values)) == NEVER_CANONICAL_NAME_TYPES


def test_observation_enum_is_the_person_literal():
    # The request enum on the wire is derived from PersonNameType, so a type
    # added to the Literal reaches producers in the same change.
    annotation = ObservationPersonName.model_fields["name_type"].annotation
    assert get_args(annotation) == PERSON_NAME_TYPES


# --- docs/NAME_TYPES.md is held to the registry ------------------------------

_DOC = Path("docs/NAME_TYPES.md")


def _doc_rows(header_prefix: str) -> list[list[str]]:
    """Cells of the markdown table whose header row starts with *header_prefix*."""
    lines = _DOC.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header_prefix))
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def test_doc_person_reference_table_matches_the_registry():
    rows = _doc_rows("| Type | Meaning | Use when | Not for | Example |")
    assert [r[0] for r in rows] == [f"`{s.slug}`" for s in PERSON_NAME_TYPE_SPECS]
    for row, spec in zip(rows, PERSON_NAME_TYPE_SPECS, strict=True):
        assert row[1:] == [spec.meaning, spec.use_when, spec.not_for, spec.example], spec.slug


def test_doc_behaviour_matrix_matches_the_registry():
    rows = _doc_rows("| Type | Ladder |")
    assert [r[0] for r in rows] == [f"`{s.slug}`" for s in PERSON_NAME_TYPE_SPECS]
    for row, s in zip(rows, PERSON_NAME_TYPE_SPECS, strict=True):
        assert row[1:] == [
            "—" if s.display_rank is None else str(s.display_rank),
            _yn(s.auto_promote),
            _yn(s.never_canonical),
            f"`{s.landing_visibility}`",
            _yn(s.renders_row),
            _yn(s.merge_distinct),
            _yn(s.decomposable),
            _yn(s.presentable),
        ], s.slug


def test_doc_org_reference_table_matches_the_registry():
    text = _DOC.read_text()
    org_section = text[text.index("## Organization name types") :]
    lines = [line for line in org_section.splitlines() if line.startswith("| `")]
    assert [line.split("|")[1].strip() for line in lines] == [
        f"`{s.slug}`" for s in ORG_NAME_TYPE_SPECS
    ]
    for line, spec in zip(lines, ORG_NAME_TYPE_SPECS, strict=True):
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert cells[1:] == [spec.meaning, spec.use_when, spec.not_for, spec.example], spec.slug
