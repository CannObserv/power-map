"""Admin voice-embeddings section for the Person detail view (#284).

Read-only surface: list a person's embeddings (aggregated across every
registered model), copy the full vector, soft-archive (delete), restore, and
hard-delete (requires an already-archived row). Manual create/paste-in is
intentionally out of scope — see docs/plans/2026-07-08-person-embeddings-section-design.md.

Table names come only from the startup-loaded embedding registry
(``app.state.embedding_registry``), never from user input — the same
injection-safe pattern the public embeddings API uses.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.embedding_registry import EmbeddingRegistry, ModelMeta

router = APIRouter(prefix="/people/{person_id}/embeddings", tags=["admin-person-embeddings"])
templates = Jinja2Templates(directory="src/templates")


def _get_registry(request: Request) -> EmbeddingRegistry:
    """Return the startup-loaded embedding model registry from app state."""
    return request.app.state.embedding_registry


def _require_model(model_id: str, registry: EmbeddingRegistry) -> ModelMeta:
    """Return the model descriptor or raise 404 for an unknown model id."""
    meta = registry.get(model_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Unknown embedding model")
    return meta


async def fetch_person_embeddings(
    db, registry: EmbeddingRegistry, person_id: str, *, include_archived: bool
) -> tuple[list[dict], int]:
    """Aggregate a person's embeddings across all registered model tables.

    Returns ``(rows, archived_count)``. Each row carries its ``model_id`` and a
    ``vector_preview`` (first 10 chars of the pgvector literal); the full vector
    is fetched on demand by the copy endpoint. ``archived_count`` always counts
    archived rows regardless of ``include_archived`` (drives the toggle label).
    """
    rows: list[dict] = []
    archived_count = 0
    for meta in registry.all():
        table = meta.table_name  # registry-controlled — not user input
        recs = await db.fetch(
            f"""
            SELECT id, left(embedding::text, 10) AS vector_preview,
                   source_service, source_job_id, source_segment,
                   recorded_at, created_at, archived_at
              FROM {table}
             WHERE person_id = $1 AND ($2 OR archived_at IS NULL)
             ORDER BY created_at DESC
            """,
            person_id,
            include_archived,
        )
        for r in recs:
            rows.append({**dict(r), "model_id": meta.model_id})
        archived_count += await db.fetchval(
            f"SELECT count(*) FROM {table} WHERE person_id = $1 AND archived_at IS NOT NULL",
            person_id,
        )
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows, archived_count


async def _render_rows(request, db, registry, person_id, *, show_archived, extra_headers=None):
    """Re-render the embeddings ``<tbody>`` partial after a mutation."""
    rows, _ = await fetch_person_embeddings(db, registry, person_id, include_archived=show_archived)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_embedding_rows.html",
        {"embeddings": rows, "person_id": person_id, "show_archived_embeddings": show_archived},
        headers=extra_headers,
    )


@router.get("/{model_id}/{embedding_id}/vector/", response_class=PlainTextResponse)
async def embedding_vector(
    person_id: str,
    model_id: str,
    embedding_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
) -> PlainTextResponse:
    """Return the full pgvector literal for one embedding (copy-to-clipboard source)."""
    meta = _require_model(model_id, _get_registry(request))
    table = meta.table_name  # registry-controlled — not user input
    vec = await db.fetchval(
        f"SELECT embedding::text FROM {table} WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if vec is None:
        raise HTTPException(status_code=404, detail="Embedding not found")
    return PlainTextResponse(vec)


@router.delete("/{model_id}/{embedding_id}/")
async def archive_embedding(
    person_id: str,
    model_id: str,
    embedding_id: str,
    request: Request,
    show_archived_embeddings: bool = Query(False),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Soft-delete an embedding by setting ``archived_at``. 409 if already archived."""
    registry = _get_registry(request)
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input
    existing = await db.fetchrow(
        f"SELECT archived_at FROM {table} WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Embedding not found")
    if existing["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="Embedding is already archived")
    await db.execute(
        f"UPDATE {table} SET archived_at = now() WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return await _render_rows(
        request,
        db,
        registry,
        person_id,
        show_archived=show_archived_embeddings,
        extra_headers=flash_trigger("success", "Embedding archived."),
    )


@router.post("/{model_id}/{embedding_id}/restore/")
async def restore_embedding(
    person_id: str,
    model_id: str,
    embedding_id: str,
    request: Request,
    show_archived_embeddings: bool = Query(False),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Restore a soft-deleted embedding by clearing ``archived_at``. 409 if already active."""
    registry = _get_registry(request)
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input
    existing = await db.fetchrow(
        f"SELECT archived_at FROM {table} WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Embedding not found")
    if existing["archived_at"] is None:
        raise HTTPException(status_code=409, detail="Embedding is already active")
    await db.execute(
        f"UPDATE {table} SET archived_at = NULL WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return await _render_rows(
        request,
        db,
        registry,
        person_id,
        show_archived=show_archived_embeddings,
        extra_headers=flash_trigger("success", "Embedding restored."),
    )


@router.delete("/{model_id}/{embedding_id}/permanent/")
async def hard_delete_embedding(
    person_id: str,
    model_id: str,
    embedding_id: str,
    request: Request,
    show_archived_embeddings: bool = Query(False),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Permanently delete an embedding. Requires it be archived first — 409 otherwise."""
    registry = _get_registry(request)
    meta = _require_model(model_id, registry)
    table = meta.table_name  # registry-controlled — not user input
    existing = await db.fetchrow(
        f"SELECT archived_at FROM {table} WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Embedding not found")
    if existing["archived_at"] is None:
        raise HTTPException(
            status_code=409, detail="Archive the embedding before deleting it permanently"
        )
    await db.execute(
        f"DELETE FROM {table} WHERE id = $1 AND person_id = $2",
        embedding_id,
        person_id,
    )
    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
    return await _render_rows(
        request,
        db,
        registry,
        person_id,
        show_archived=show_archived_embeddings,
        extra_headers=flash_trigger("success", "Embedding permanently deleted."),
    )
