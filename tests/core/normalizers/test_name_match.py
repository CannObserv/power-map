"""The create hint's name matching (#533).

A literal comparison missed 4 of 17 duplicate people in #501's triage. Each
tier here is named for what it compared, so a triage record can say whether a
hint rests on the same text or on a looser reading of it.
"""

import pytest

from src.core.normalizers.name_match import TIERS, NameIndex, fold, org_match, person_match

# --- fold ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("E. G. “Pat” Patterson", 'e. g. "pat" patterson'),
        ("O’Brien", "o'brien"),
        ("  Patty   Murray ", "patty murray"),
        ("PATTY MURRAY", "patty murray"),
        ("José Núñez", "jose nunez"),
        ("Mary Smith–Jones", "mary smith-jones"),
    ],
)
def test_fold_normalises_formatting_only(raw, folded):
    assert fold(raw) == folded


@pytest.mark.parametrize("raw", ["Noble Farms (Tacoma)", "Mary Brown (2)", "Robert (Bob) Smith"])
def test_fold_keeps_parentheticals(raw):
    """A parenthetical is often what tells two entities apart, so `exact` never drops one."""
    assert fold(raw) == raw.casefold()


# --- person tiers: #533's four misses, and the 13 it caught ---------------------


@pytest.mark.parametrize(
    ("producer", "pm", "tier"),
    [
        ("Patty Murray", "Patty Murray", "exact"),
        ("E. G. “Pat” Patterson", 'E. G. "Pat" Patterson', "exact"),
        ("PATTY MURRAY", "Patty Murray", "exact"),
        ("Gerald L. “Jerry” Saling", 'Gerald "Jerry" Saling', "given"),
        ("Stanley C. Johnson", "Stanley Johnson", "given"),
        ("Myron “Mike” Kreidler", "Mike Kreidler", "given"),
        (
            "Myron “Mike” Kreidler (On leave of absence for military duty Jan. 8, 1991"
            " to April 18, 1991)",
            "Mike Kreidler",
            "given",
        ),
        ("Murray, Patty", "Patty Murray", "given"),
        ("Mary Brown (2)", "Mary Brown", "given"),  # a trailing note is not the same text
        ("E. Patterson", "Edward Patterson", "initial"),
        ("Michael Kreidler", "Mike Kreidler", "nickname"),
    ],
)
def test_a_twin_matches_at_the_tier_that_explains_it(producer, pm, tier):
    assert person_match(producer, pm) == tier


@pytest.mark.parametrize(
    ("producer", "pm"),
    [
        ("N. B. Atkinson", "N. P. Atkinson"),  # adjudicated distinct on five grounds (usa-wa)
        ("Stanley Johnson", "Samuel Johnson"),
        ("Gerald Saling", "Geraldine Saling"),
        ("John Smith", "Jane Smith"),
        ("John Smith Jr.", "John Smith Sr."),
        ("Patty Murray", "Patty Morrison"),
    ],
)
def test_distinct_people_do_not_match(producer, pm):
    assert person_match(producer, pm) is None


def test_a_middle_initial_absent_on_one_side_is_not_a_conflict():
    """What separates Johnson (a twin) from Atkinson (two people): absent, not different."""
    assert person_match("Stanley C. Johnson", "Stanley Johnson") == "given"
    assert person_match("Stanley C. Johnson", "Stanley D. Johnson") is None


def test_a_name_without_a_surname_matches_only_exactly():
    assert person_match("Cher", "Cher") == "exact"
    assert person_match("Cher", "Cher Bono") is None


# --- organization tiers ------------------------------------------------------


@pytest.mark.parametrize(
    ("producer", "pm", "tier"),
    [
        ("Liquor and Cannabis Board", "LIQUOR AND CANNABIS BOARD", "exact"),
        ("Microsoft Corporation", "Microsoft Corp.", "legal_form"),
        ("Acme Holdings, L.L.C.", "ACME HOLDINGS LLC", "legal_form"),
        ("Liquor & Cannabis Board", "Liquor and Cannabis Board", "legal_form"),
    ],
)
def test_an_org_twin_matches_at_its_tier(producer, pm, tier):
    assert org_match(producer, pm) == tier


@pytest.mark.parametrize(
    ("producer", "pm"),
    [
        ("Washington State Democratic Party", "Washington State Republican Party"),
        ("Committee on Finance", "Committee on Health Care"),
        ("Noble Farms (Tacoma)", "Noble Farms"),  # a location telling two branches apart
    ],
)
def test_distinct_orgs_do_not_match(producer, pm):
    assert org_match(producer, pm) is None


@pytest.mark.parametrize(("producer", "pm"), [("LLC", "Inc."), ("Co.", "Company")])
def test_names_that_are_only_a_legal_form_do_not_match_each_other(producer, pm):
    """cleanco strips the whole name, so an empty base must not read as agreement."""
    assert org_match(producer, pm) is None


# --- the index the hint looks names up in ----------------------------------------


def test_the_index_names_each_parent_at_its_strongest_tier():
    index = NameIndex("person")
    index.add("Mike Kreidler", "01MA")
    index.add("Myron Kreidler", "01MA")  # same parent, a second name
    index.add("Michael Kreidler", "01MB")
    index.add("Patty Murray", "01MC")

    assert index.lookup("Myron “Mike” Kreidler") == {"01MA": "given", "01MB": "nickname"}


def test_a_nickname_outranks_an_initial():
    """On PM's corpus an initial is the noisiest tier: an old roster's `A. A. Smith`
    meets every modern A-named Smith, where a nickname pair is usually one person."""
    assert TIERS.index("nickname") < TIERS.index("initial")


def test_the_index_for_organizations_uses_org_tiers():
    index = NameIndex("organization")
    index.add("Microsoft Corp.", "01NA")

    assert index.lookup("Microsoft Corporation") == {"01NA": "legal_form"}


def test_an_entity_without_a_matcher_compares_folded_text_only():
    index = NameIndex("jurisdiction")
    index.add("King County", "01JA")

    assert index.lookup("KING  COUNTY") == {"01JA": "exact"}
    assert index.lookup("King Co.") == {}


def test_a_value_that_matches_nothing_finds_nothing():
    index = NameIndex("person")
    index.add("Patty Murray", "01MC")

    assert index.lookup("Nobody Known") == {}


@pytest.mark.parametrize("entity", ["person", "organization", "jurisdiction"])
def test_an_empty_name_matches_nothing(entity):
    index = NameIndex(entity)
    index.add("", "01XA")
    index.add("   ", "01XB")
    index.add("Patty Murray", "01XC")

    assert index.lookup("") == {}
    assert index.lookup("  ") == {}
