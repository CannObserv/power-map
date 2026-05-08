"""Tests for the person-name decomposer (suggest_parts / suggest_locale_script).

Decomposer is *suggest-only* — never persists. Backed by `nameparser.HumanName`
for `script == 'Latn'`; emits ``confidence='skip'`` for any other script (today
the dataset is 100% Latn, see issue #135).
"""

import pytest

from src.core.normalizers.person_name import (
    PartsSuggestion,
    suggest_locale_script,
    suggest_parts,
)


def _suggest(name: str, *, locale: str = "en-US", script: str = "Latn",
             name_type: str = "legal") -> PartsSuggestion:
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
        s.given_names = ["Other"]   # frozen=True
