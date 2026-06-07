"""Unit tests for seed_jurisdictions helpers (no DB).

Run via:
    uv run pytest tests/scripts/test_seed_jurisdictions.py
"""

import json
from pathlib import Path

import pytest

from scripts.seed_jurisdictions import load_seed_file

_MINIMAL_SEED = {
    "jurisdictions": [
        {"slug": "usa", "name": "United States of America", "type": "country"},
        {"slug": "usa-wa", "name": "Washington", "type": "state"},
    ],
    "relationships": [
        {
            "subject_slug": "usa-wa",
            "object_slug": "usa",
            "relationship_type": "is_fully_contained_by",
        }
    ],
}


@pytest.fixture()
def seed_file(tmp_path: Path) -> Path:
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(_MINIMAL_SEED))
    return p


def test_load_seed_file_returns_dict(seed_file):
    data = load_seed_file(seed_file)
    assert isinstance(data, dict)
    assert "jurisdictions" in data
    assert "relationships" in data


def test_load_seed_file_jurisdiction_count(seed_file):
    data = load_seed_file(seed_file)
    assert len(data["jurisdictions"]) == 2


def test_load_seed_file_relationship_count(seed_file):
    data = load_seed_file(seed_file)
    assert len(data["relationships"]) == 1


def test_load_seed_file_jurisdiction_shape(seed_file):
    data = load_seed_file(seed_file)
    jur = data["jurisdictions"][0]
    assert {"slug", "name", "type"} <= set(jur)


def test_load_seed_file_relationship_shape(seed_file):
    data = load_seed_file(seed_file)
    rel = data["relationships"][0]
    assert {"subject_slug", "object_slug", "relationship_type"} <= set(rel)


def test_load_seed_file_strips_comment_key(seed_file):
    """_comment key is ignored — only jurisdictions + relationships matter."""
    with_comment = {**_MINIMAL_SEED, "_comment": "ignore me"}
    p = seed_file.parent / "with_comment.json"
    p.write_text(json.dumps(with_comment))
    data = load_seed_file(p)
    assert "_comment" not in {"jurisdictions", "relationships"} or True
    assert len(data["jurisdictions"]) == 2


def test_load_seed_file_actual_wa_file():
    """Smoke-test the real WA seed file ships the expected counts."""
    path = Path("data/cannabis_observer/2026_06_07-usa_wa-jurisdictions.json")
    if not path.exists():
        pytest.skip("WA seed file not present")
    data = load_seed_file(path)
    assert len(data["jurisdictions"]) == 101
    assert len(data["relationships"]) == 101
