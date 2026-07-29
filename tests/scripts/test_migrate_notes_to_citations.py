"""Unit tests for the notes→citations URL extractor (#319)."""

from scripts.migrate_notes_to_citations import extract_urls


def test_extracts_single_url():
    assert extract_urls("Source: https://housedemocrats.wa.gov/jinkins/2019/07/31/") == [
        "https://housedemocrats.wa.gov/jinkins/2019/07/31/"
    ]


def test_trims_trailing_punctuation():
    assert extract_urls("see (https://example.com/a).") == ["https://example.com/a"]


def test_dedupes_preserving_order():
    notes = "https://a.test/1 and https://b.test/2 and https://a.test/1 again"
    assert extract_urls(notes) == ["https://a.test/1", "https://b.test/2"]


def test_no_url_returns_empty():
    assert extract_urls("Tenure 2021-23 (from legacy title) — dates need review") == []
