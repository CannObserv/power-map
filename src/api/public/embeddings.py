"""Public API v1 — voice embedding endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_scope
from src.api.public.schemas import (
    EmbeddingArchiveResponse,
    EmbeddingBatchArchiveResponse,
    EmbeddingListItem,
    EmbeddingListResponse,
    EmbeddingWriteRequest,
    EmbeddingWriteResponse,
    IdentifyRequest,
    IdentifyResponse,
    SearchMeta,
    fmt_ts,
)
from src.core.db import generate_id
from src.core.embedding_registry import EmbeddingRegistry

router = APIRouter(prefix="/people", tags=["public-api"])


def _get_registry(request: Request) -> EmbeddingRegistry:
    """Return the startup-loaded embedding model registry from app state."""
    return request.app.state.embedding_registry


def _vec_str(embedding: list[float]) -> str:
    """Format a Python list as a pgvector literal: ``[f1,f2,...]``."""
    return "[" + ",".join(str(v) for v in embedding) + "]"


@router.post(
    "/identify",
    response_model=IdentifyResponse,
    operation_id="identifyPersonByVoice",
)
async def identify_person(
    body: IdentifyRequest,
    _auth: AuthedKey = Depends(require_scope("voice_embeddings:read")),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> IdentifyResponse:
    """Return the top-k persons whose stored embeddings best match the query vector.

    Returns ``matches: []`` when the model is unknown or has no active embeddings.
    422 when the embedding dimension does not match the model's expected dimension.
    """
    top_k = min(max(body.top_k, 1), 25)

    meta = registry.get(body.model_id)
    if meta is None or not meta.is_queryable:
        return IdentifyResponse(matches=[])

    if len(body.embedding) != meta.dimension:
        raise HTTPException(
            status_code=422,
            detail=f"Embedding dimension {len(body.embedding)} does not match "
            f"model '{meta.model_id}' expected {meta.dimension}",
        )

    table = meta.table_name  # registry-controlled — not user input
    op = meta.operator
    vec = _vec_str(body.embedding)

    rows = await db.fetch(
        f"""
        SELECT e.id           AS embedding_id,
               e.person_id,
               v.display_name AS person_name,
               1 - (e.embedding {op} $1::vector) AS similarity,
               e.source_job_id,
               e.recorded_at
        FROM {table} e
        LEFT JOIN v_person_display_names v ON v.person_id = e.person_id
        WHERE e.archived_at IS NULL
        ORDER BY e.embedding {op} $1::vector
        LIMIT $2
        """,
        vec,
        top_k,
    )

    return IdentifyResponse(
        matches=[
            {
                "person_id": r["person_id"],
                "person_name": r["person_name"],
                "similarity": float(r["similarity"]),
                "embedding_id": r["embedding_id"],
                "source_job_id": r["source_job_id"],
                "recorded_at": fmt_ts(r["recorded_at"]),
            }
            for r in rows
        ]
    )


@router.post(
    "/{person_id}/embeddings",
    response_model=EmbeddingWriteResponse,
    operation_id="writePersonEmbedding",
)
async def write_person_embedding(
    person_id: str,
    body: EmbeddingWriteRequest,
    auth: AuthedKey = Depends(require_scope("voice_embeddings:write")),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> EmbeddingWriteResponse:
    """Write a voice embedding observation for a person.

    Idempotent on the (source_service, source_job_id, source_segment, person_id)
    unique constraint — a duplicate write returns 200 with the existing row's id.
    404 if the person does not exist or is archived.
    422 on dimension mismatch or unknown/write-disabled model.
    """
    meta = registry.get(body.model_id)
    if meta is None or not meta.accepts_writes:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or write-disabled embedding model '{body.model_id}'",
        )

    if len(body.embedding) != meta.dimension:
        raise HTTPException(
            status_code=422,
            detail=f"Embedding dimension {len(body.embedding)} does not match "
            f"model '{meta.model_id}' expected {meta.dimension}",
        )

    person = await db.fetchrow(
        "SELECT id, archived_at FROM people WHERE id = $1",
        person_id,
    )
    if not person or person["archived_at"] is not None:
        raise HTTPException(status_code=404, detail="Person not found")

    table = meta.table_name  # registry-controlled — not user input
    new_id = generate_id()
    vec = _vec_str(body.embedding)

    row = await db.fetchrow(
        f"""
        INSERT INTO {table}
            (id, person_id, embedding, embedding_dim,
             activity_ms, audio_sample_rate_hz,
             source_service, source_job_id, source_segment,
             recorded_at, created_by_key_id)
        VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (source_service, source_job_id, source_segment, person_id)
        DO NOTHING
        RETURNING id, created_at
        """,
        new_id,
        person_id,
        vec,
        len(body.embedding),
        body.activity_ms,
        body.audio_sample_rate_hz,
        body.source.service,
        body.source.job_id,
        body.source.segment,
        body.source.recorded_at,
        auth.key_id,
    )

    if row is None:
        # Duplicate — fetch the existing row
        row = await db.fetchrow(
            f"""
            SELECT id, created_at FROM {table}
            WHERE source_service = $1
              AND source_job_id  = $2
              AND source_segment = $3
              AND person_id      = $4
            """,
            body.source.service,
            body.source.job_id,
            body.source.segment,
            person_id,
        )

    return EmbeddingWriteResponse(
        embedding_id=row["id"],
        person_id=person_id,
        created_at=fmt_ts(row["created_at"]),
    )


def _require_model(model_id: str, registry: EmbeddingRegistry):
    """Return ModelMeta or raise 422 for unknown model_id."""
    meta = registry.get(model_id)
    if meta is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown embedding model '{model_id}'",
        )
    return meta


@router.delete(
    "/{person_id}/embeddings/{embedding_id}",
    response_model=EmbeddingArchiveResponse,
    operation_id="softDeletePersonEmbedding",
)
async def soft_delete_embedding(
    person_id: str,
    embedding_id: str,
    model_id: str = Query(...),
    _auth: AuthedKey = Depends(require_scope("voice_embeddings:write")),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> EmbeddingArchiveResponse:
    """Soft-delete a single embedding row by setting ``archived_at``.

    Idempotent — re-deleting an already-archived row returns 200 with the
    existing ``archived_at``.  404 if the embedding or person is not found.
    """
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input

    row = await db.fetchrow(
        f"""
        UPDATE {table}
           SET archived_at = COALESCE(archived_at, now())
         WHERE id = $1 AND person_id = $2
         RETURNING id, archived_at
        """,
        embedding_id,
        person_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Embedding not found")

    return EmbeddingArchiveResponse(
        embedding_id=row["id"],
        archived_at=fmt_ts(row["archived_at"]),
    )


@router.delete(
    "/{person_id}/embeddings",
    response_model=EmbeddingBatchArchiveResponse,
    operation_id="batchSoftDeletePersonEmbeddings",
)
async def batch_soft_delete_embeddings(
    person_id: str,
    model_id: str = Query(...),
    source_job_id: str = Query(...),
    _auth: AuthedKey = Depends(require_scope("voice_embeddings:write")),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> EmbeddingBatchArchiveResponse:
    """Batch soft-delete all active embeddings for ``person_id`` matching ``source_job_id``.

    Already-archived rows are skipped.  Returns ``archived_count`` (may be 0).
    """
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input

    rows = await db.fetch(
        f"""
        UPDATE {table}
           SET archived_at = now()
         WHERE person_id = $1 AND source_job_id = $2 AND archived_at IS NULL
         RETURNING id
        """,
        person_id,
        source_job_id,
    )
    return EmbeddingBatchArchiveResponse(archived_count=len(rows))


@router.post(
    "/{person_id}/embeddings/{embedding_id}/restore",
    response_model=EmbeddingArchiveResponse,
    operation_id="restorePersonEmbedding",
)
async def restore_embedding(
    person_id: str,
    embedding_id: str,
    model_id: str = Query(...),
    _auth: AuthedKey = Depends(require_scope("voice_embeddings:write")),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> EmbeddingArchiveResponse:
    """Restore a soft-deleted embedding by clearing ``archived_at``.

    404 if the embedding is not found.  409 if it is already active.
    """
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input

    existing = await db.fetchrow(
        f"SELECT id, archived_at FROM {table} WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Embedding not found")
    if existing["archived_at"] is None:
        raise HTTPException(status_code=409, detail="Embedding is already active")

    await db.execute(
        f"UPDATE {table} SET archived_at = NULL WHERE id = $1",
        embedding_id,
    )
    return EmbeddingArchiveResponse(embedding_id=embedding_id, archived_at=None)


@router.get(
    "/{person_id}/embeddings",
    response_model=EmbeddingListResponse,
    operation_id="listPersonEmbeddings",
)
async def list_person_embeddings(
    person_id: str,
    model_id: str = Query(...),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _auth: AuthedKey = Depends(require_scope("voice_embeddings:read")),
    db=Depends(get_db),
    registry: EmbeddingRegistry = Depends(_get_registry),
) -> EmbeddingListResponse:
    """List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.
    """
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input

    rows = await db.fetch(
        f"""
        SELECT id, source_job_id, source_segment, recorded_at,
               activity_ms, archived_at, created_at
          FROM {table}
         WHERE person_id = $1
           AND ($2 OR archived_at IS NULL)
         ORDER BY created_at DESC
         LIMIT $3 OFFSET $4
        """,
        person_id,
        include_archived,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    count_row = await db.fetchrow(
        f"""
        SELECT count(*) AS n FROM {table}
         WHERE person_id = $1 AND ($2 OR archived_at IS NULL)
        """,
        person_id,
        include_archived,
    )

    return EmbeddingListResponse(
        data=[
            EmbeddingListItem(
                embedding_id=r["id"],
                model_id=model_id,
                source_job_id=r["source_job_id"],
                source_segment=r["source_segment"],
                recorded_at=fmt_ts(r["recorded_at"]),
                activity_ms=r["activity_ms"],
                archived_at=fmt_ts(r["archived_at"]),
                created_at=fmt_ts(r["created_at"]),
            )
            for r in page
        ],
        meta=SearchMeta(
            limit=limit,
            offset=offset,
            count=count_row["n"],
            has_more=has_more,
        ),
    )
