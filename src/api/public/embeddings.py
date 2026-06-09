"""Public API v1 — voice embedding endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_scope
from src.api.public.schemas import (
    EmbeddingWriteRequest,
    EmbeddingWriteResponse,
    IdentifyRequest,
    IdentifyResponse,
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

    table = meta.table_name
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

    table = meta.table_name
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
