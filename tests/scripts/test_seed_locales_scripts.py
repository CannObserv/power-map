"""Unit tests for the locale/script seed helpers (no DB).

Skipped when the `seed` dependency group isn't installed (default
runtime env). Run via `uv run --group seed pytest tests/scripts/...`.
"""

import pytest

pytest.importorskip("langcodes")
pytest.importorskip("pycountry")

from scripts.seed_locales_scripts import (  # noqa: E402
    enumerate_bcp47_locales,
    enumerate_iso15924_scripts,
)


def test_enumerate_bcp47_locales_yields_dict_records():
    rows = list(enumerate_bcp47_locales())
    assert len(rows) > 1000, f"expected at least 1000 CLDR locales, got {len(rows)}"
    sample = rows[0]
    assert {"code", "language", "script", "region", "display_name"} <= set(sample)


def test_enumerate_bcp47_locales_includes_common_codes():
    """Sanity check: well-known codes resolve in the enumeration."""
    rows = list(enumerate_bcp47_locales())
    by_code = {r["code"]: r for r in rows}
    for code in ("en", "es", "ja", "is"):
        assert code in by_code, f"common locale {code} missing from enumeration"


def test_enumerate_bcp47_locales_codes_unique():
    rows = list(enumerate_bcp47_locales())
    codes = [r["code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate code in seed enumeration"


def test_enumerate_bcp47_locales_display_name_non_empty():
    rows = list(enumerate_bcp47_locales())
    empty = [r for r in rows if not r["display_name"]]
    assert not empty, f"{len(empty)} locales had empty display_name"


def test_enumerate_iso15924_scripts_full_set():
    rows = list(enumerate_iso15924_scripts())
    assert len(rows) >= 180, f"expected ~200 ISO 15924 codes, got {len(rows)}"
    sample = rows[0]
    assert {"code", "numeric_code", "name"} <= set(sample)


def test_enumerate_iso15924_scripts_includes_common_codes():
    rows = list(enumerate_iso15924_scripts())
    by_code = {r["code"]: r for r in rows}
    for code in ("Latn", "Hans", "Hant", "Cyrl", "Arab"):
        assert code in by_code, f"common script {code} missing from enumeration"


def test_iso15924_numeric_codes_unique():
    rows = list(enumerate_iso15924_scripts())
    codes = [r["numeric_code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate numeric_code in seed enumeration"


def test_iso15924_codes_are_four_letter():
    rows = list(enumerate_iso15924_scripts())
    for r in rows:
        assert len(r["code"]) == 4, f"non-4-letter code: {r['code']!r}"
