"""Unit tests for src.api.public.people._fetch_detail_arrays.

Fast, DB-free tests that pin the embedding-count consolidation: a fixed number
of round-trips regardless of how many queryable models the registry holds.
"""

from unittest.mock import AsyncMock

from src.api.public.people import _fetch_detail_arrays
from src.core.embedding_registry import EmbeddingRegistry, ModelMeta


def _meta(model_id: str, table_name: str) -> ModelMeta:
    return ModelMeta(
        model_id=model_id,
        table_name=table_name,
        dimension=256,
        metric="cosine",
        accepts_writes=False,
        is_queryable=True,
        operator="<=>",
    )


async def test_fetch_detail_arrays_single_round_trip_regardless_of_model_count():
    """Embedding counts collapse into one query — never one-per-model."""
    db = AsyncMock()
    db.fetch.return_value = []

    registry = EmbeddingRegistry(
        {
            "m1": _meta("m1", "person_embeddings_m1"),
            "m2": _meta("m2", "person_embeddings_m2"),
            "m3": _meta("m3", "person_embeddings_m3"),
        }
    )

    await _fetch_detail_arrays("pid", db, registry)

    # names + identifiers + exactly one consolidated count query = 3 fetches,
    # independent of the 3 queryable models. A regression to the per-model
    # loop would bump this (or reintroduce fetchval calls) and fail here.
    assert db.fetch.call_count == 3
    db.fetchval.assert_not_called()

    count_sql = db.fetch.call_args_list[-1].args[0]
    assert count_sql.count("UNION ALL") == 2
    for table in ("person_embeddings_m1", "person_embeddings_m2", "person_embeddings_m3"):
        assert table in count_sql


async def test_fetch_detail_arrays_no_count_query_when_no_queryable_models():
    """Empty registry issues no count query and returns 0 (guards the empty-SQL branch)."""
    db = AsyncMock()
    db.fetch.return_value = []

    _, _, voice_count = await _fetch_detail_arrays("pid", db, EmbeddingRegistry({}))

    assert voice_count == 0
    # Only names + identifiers — no count query, no per-model fetchval.
    assert db.fetch.call_count == 2
    db.fetchval.assert_not_called()


async def test_fetch_detail_arrays_skips_non_queryable_models():
    """Non-queryable models are excluded from the consolidated count query."""
    db = AsyncMock()
    db.fetch.return_value = []

    queryable = _meta("m1", "person_embeddings_m1")
    not_queryable = ModelMeta(
        model_id="m2",
        table_name="person_embeddings_m2",
        dimension=256,
        metric="cosine",
        accepts_writes=False,
        is_queryable=False,
        operator="<=>",
    )
    registry = EmbeddingRegistry({"m1": queryable, "m2": not_queryable})

    await _fetch_detail_arrays("pid", db, registry)

    count_sql = db.fetch.call_args_list[-1].args[0]
    assert "UNION ALL" not in count_sql
    assert "person_embeddings_m1" in count_sql
    assert "person_embeddings_m2" not in count_sql
