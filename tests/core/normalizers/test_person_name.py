"""Tests for the person-name decomposer (suggest_parts / suggest_locale_script).

Decomposer is *suggest-only* — never persists. Backed by `nameparser.parse`
for `script == 'Latn'`; emits ``confidence='skip'`` for any other script (today
the dataset is 100% Latn, see issue #135).
"""

import warnings

import pytest

from src.core.normalizers.person_name import (
    _INITIAL_RE,
    PartsSuggestion,
    _split_middle,
    suggest_locale_script,
    suggest_parts,
)


def _suggest(
    name: str, *, locale: str = "en-US", script: str = "Latn", name_type: str = "legal"
) -> PartsSuggestion:
    return suggest_parts(name, locale=locale, script=script, name_type=name_type)


# ---- name_type gating ------------------------------------------------------


@pytest.mark.parametrize("nt", ["initials", "mrz", "reading", "romanization"])
def test_non_decomposable_name_types_return_skip(nt):
    s = _suggest("Jane Doe", name_type=nt)
    assert s.confidence == "skip"
    assert any(f"name_type={nt}" in r for r in s.reasons)
    assert s.given_names == []
    assert s.family_names == []
    assert s.primary_identifier is None


# ---- script gating ---------------------------------------------------------


@pytest.mark.parametrize("script", ["Hant", "Hans", "Cyrl", "Arab", "Kana"])
def test_non_latn_scripts_return_skip(script):
    s = _suggest("Some Name", script=script)
    assert s.confidence == "skip"
    assert any("unsupported-script" in r for r in s.reasons)


# ---- empty / whitespace input ---------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_empty_input_returns_skip(raw):
    s = _suggest(raw)
    assert s.confidence == "skip"
    assert any("empty-name" in r for r in s.reasons)


# ---- trivial cases ---------------------------------------------------------


def test_two_token_first_last_is_trivial():
    s = _suggest("Jane Doe")
    assert s.confidence == "trivial"
    assert s.given_names == ["Jane"]
    assert s.family_names == ["Doe"]
    assert s.additional_names == []
    assert s.honorific_prefix is None
    assert s.honorific_suffix is None
    assert s.primary_identifier == "family"


def test_three_token_with_initial_middle_is_trivial():
    s = _suggest("John A. Doe")
    assert s.confidence == "trivial"
    assert s.given_names == ["John"]
    assert s.additional_names == ["A."]
    assert s.family_names == ["Doe"]
    assert s.primary_identifier == "family"


def test_honorific_prefix_extracted():
    s = _suggest("Dr. Jane Doe")
    assert s.confidence == "trivial"
    assert s.honorific_prefix == "Dr."
    assert s.given_names == ["Jane"]
    assert s.family_names == ["Doe"]


def test_honorific_suffix_extracted():
    s = _suggest("Jane Doe Jr.")
    assert s.confidence == "trivial"
    assert s.honorific_suffix == "Jr."
    assert s.given_names == ["Jane"]
    assert s.family_names == ["Doe"]


def test_honorific_suffix_keeps_generation_and_credentials():
    """3.0 splits `generation` from `credentials`; the suggestion keeps the whole suffix (#782)."""
    s = _suggest("Jane Doe III, Ph.D.")
    assert s.honorific_suffix == "III, Ph.D."
    assert s.given_names == ["Jane"]
    assert s.family_names == ["Doe"]


def test_decomposing_emits_no_deprecation_warning():
    """2.8's HumanName warns on every construction, and 3.0 removes it (#782)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert _suggest("Dr. Jane A. Doe Jr.").family_names == ["Doe"]


def test_combined_prefix_and_suffix():
    s = _suggest("Dr. Jane A. Doe Jr.")
    assert s.confidence == "trivial"
    assert s.honorific_prefix == "Dr."
    assert s.honorific_suffix == "Jr."
    assert s.given_names == ["Jane"]
    assert s.additional_names == ["A."]
    assert s.family_names == ["Doe"]


def test_surname_particle_kept_in_family_names():
    """`van der`, `de la`, `de`, etc. stay glued to the surname compound."""
    s = _suggest("Hans van der Berg")
    assert s.confidence == "trivial"
    assert s.given_names == ["Hans"]
    assert s.family_names == ["van der Berg"]
    assert any("particle" in r for r in s.reasons)


def test_de_la_compound_kept_together():
    s = _suggest("Maria de la Cruz")
    assert s.confidence == "trivial"
    assert s.given_names == ["Maria"]
    assert s.family_names == ["de la Cruz"]


def test_hyphenated_surname_single_token():
    s = _suggest("Jane Smith-Jones")
    assert s.confidence == "trivial"
    assert s.given_names == ["Jane"]
    assert s.family_names == ["Smith-Jones"]


def test_initials_chain_treated_as_given_plus_additional():
    s = _suggest("J. R. R. Tolkien")
    assert s.confidence == "trivial"
    assert s.given_names == ["J."]
    assert s.additional_names == ["R.", "R."]
    assert s.family_names == ["Tolkien"]


def test_a_dotted_initials_run_reads_like_the_spaced_chain():
    """nameparser 3.1 splits a dotted run after its first initial (3.0 kept ``J.R.R.``
    whole as the given name, #868). ``_split_middle`` splits the rest of the run
    into one additional name per initial, so the run reads as the spaced chain
    above does (#870)."""
    s = _suggest("J.R.R. Tolkien")
    assert s == _suggest("J. R. R. Tolkien")
    assert s.confidence == "trivial"
    assert s.given_names == ["J."]
    assert s.additional_names == ["R.", "R."]
    assert s.family_names == ["Tolkien"]


def test_a_dotted_initials_run_beside_a_word_stays_ambiguous():
    """Confidence pin (#870): splitting the run turns ``R.R.`` into two initials,
    but a non-initial beside them still makes the middle ambiguous."""
    s = _suggest("J.R.R. Mary Smith")
    assert s.additional_names == ["R.", "R.", "Mary"]
    assert s.confidence == "ambiguous"
    assert "multi-token-middle" in s.reasons


@pytest.mark.parametrize(
    ("middle", "expected"),
    [
        ("R.R.", ["R.", "R."]),
        ("B.C.D.", ["B.", "C.", "D."]),
        ("R.R. Mary", ["R.", "R.", "Mary"]),
        ("R. R.", ["R.", "R."]),
        ("Mary Anne", ["Mary", "Anne"]),
        ("", []),
        # Left whole: only a run of `_INITIAL_RE`'s shape (an uppercase ASCII
        # letter plus a dot, two or more times) is split.
        ("St.", ["St."]),
        ("Jr.", ["Jr."]),
        ("Ph.D.", ["Ph.D."]),
        ("r.r.", ["r.r."]),
        ("É.É.", ["É.É."]),
        ("R.R", ["R.R"]),
        ("RR.", ["RR."]),
    ],
)
def test_split_middle_splits_only_a_dotted_initials_run(middle, expected):
    assert _split_middle(middle) == expected


def test_every_initial_split_from_a_run_is_an_initial():
    """CR 1 (#870): the run rule and ``_INITIAL_RE`` move together. Every run
    ``_split_middle`` splits must yield elements ``_INITIAL_RE`` matches, or the
    confidence rule reads the split initials as words. Widening one regex and
    not the other fails here, over the whole Latin range, not a list of rows."""
    split = 0
    for cp in range(0x20, 0x250):
        letter = chr(cp)
        if not letter.isalpha():
            continue
        run = f"{letter}.{letter}."
        parts = _split_middle(run)
        if parts == [run]:
            continue
        split += 1
        assert all(_INITIAL_RE.match(part) for part in parts), (run, parts)
    assert split == 26  # A–Z today; update with the regexes if they widen


def test_md_opening_a_name_is_the_given_name_not_an_honorific():
    """Md. is Muhammad written short: a given name from nameparser 3.1, a title in 3.0 (#868)."""
    s = _suggest("Md. Rahman")
    assert s.honorific_prefix is None
    assert s.given_names == ["Md."]
    assert s.family_names == ["Rahman"]


def test_lastname_first_comma_form():
    s = _suggest("Doe, Jane")
    assert s.confidence == "trivial"
    assert s.given_names == ["Jane"]
    assert s.family_names == ["Doe"]


# ---- mononym ---------------------------------------------------------------


def test_mononym_single_clean_token():
    s = _suggest("Cher")
    assert s.confidence == "trivial"
    assert s.given_names == ["Cher"]
    assert s.family_names == []
    assert s.primary_identifier == "mononym"


def test_mononym_with_diacritics():
    s = _suggest("Beyoncé")
    assert s.confidence == "trivial"
    assert s.primary_identifier == "mononym"
    assert s.given_names == ["Beyoncé"]


# ---- ambiguous cases -------------------------------------------------------


def test_four_plus_tokens_without_particle_is_ambiguous():
    """Could be multi-word given name OR compound surname — needs review."""
    s = _suggest("Mary Jane Watson Parker")
    assert s.confidence == "ambiguous"
    assert any("multi-token" in r for r in s.reasons)


def test_single_hyphenated_token_is_ambiguous():
    """`Smith-Jones` alone could be a hyphenated surname OR a strange given name."""
    s = _suggest("Smith-Jones")
    assert s.confidence == "ambiguous"
    assert any("hyphenated-single-token" in r for r in s.reasons)


# ---- locale/script suggestion (phase 1 helper) -----------------------------


def test_suggest_locale_script_defaults_pure_ascii_to_en_us_latn():
    """Pure ASCII names get the safe default."""
    locale, script = suggest_locale_script("Jane Doe")
    assert locale == "en-US"
    assert script == "Latn"


def test_suggest_locale_script_skips_cjk_entirely():
    """CJK / non-Latin scripts: both fields escalated."""
    locale, script = suggest_locale_script("毛澤東")
    assert locale is None
    assert script is None


@pytest.mark.parametrize("script_name", ["Иван Иванов", "ヒロシ", "محمد", "한글"])
def test_suggest_locale_script_skips_other_non_latin_scripts(script_name):
    locale, script = suggest_locale_script(script_name)
    assert locale is None
    assert script is None


@pytest.mark.parametrize(
    "name",
    [
        "Pedro García",
        "João Castel-Branco Goulão",
        "Jürgen Unützer",
        "André Unicume",
        "Adán Espino",
        "Joey Peña",
    ],
)
def test_suggest_locale_script_latin_with_diacritics_keeps_script_drops_locale(name):
    """Latin-with-diacritics: script is unambiguously Latn, locale is a
    judgment call (es vs. pt vs. de vs. fr…) — keep script, escalate locale.
    """
    locale, script = suggest_locale_script(name)
    assert locale is None
    assert script == "Latn"


def test_suggest_locale_script_empty_input_skips():
    locale, script = suggest_locale_script("")
    assert locale is None
    assert script is None


# ---- PartsSuggestion shape -------------------------------------------------


def test_skip_constructor_produces_well_formed_skip():
    s = PartsSuggestion.skip("test-reason")
    assert s.confidence == "skip"
    assert s.reasons == ["test-reason"]
    assert s.given_names == []
    assert s.family_names == []
    assert s.additional_names == []
    assert s.honorific_prefix is None
    assert s.honorific_suffix is None
    assert s.primary_identifier is None


def test_suggestion_is_frozen_dataclass():
    """PartsSuggestion must be immutable so callers can't mutate the lists."""
    s = _suggest("Jane Doe")
    with pytest.raises((AttributeError, Exception)):
        s.given_names = ["Other"]  # frozen=True
