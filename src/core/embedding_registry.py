"""Embedding model registry — startup-loaded cache of model metadata.

Loaded once at app startup from the ``embedding_model_registry`` table and
stored in ``app.state.embedding_registry``.  Route handlers access it via the
``get_embedding_registry`` FastAPI dependency, which can be overridden in tests.
"""

from dataclasses import dataclass
from typing import Literal

VectorMetric = Literal["cosine", "l2", "inner_product"]

_OPERATORS: dict[str, str] = {
    "cosine": "<=>",
    "l2": "<->",
    "inner_product": "<#>",
}


@dataclass(frozen=True)
class ModelMeta:
    """Immutable descriptor for one registered embedding model."""

    model_id: str
    table_name: str
    dimension: int
    metric: VectorMetric
    accepts_writes: bool
    is_queryable: bool
    operator: str


class EmbeddingRegistry:
    """In-memory cache of all registered embedding models."""

    def __init__(self, models: dict[str, ModelMeta]) -> None:
        self._models = models

    def get(self, model_id: str) -> ModelMeta | None:
        """Return the model descriptor or None if unknown."""
        return self._models.get(model_id)

    def all(self) -> list[ModelMeta]:
        """Return all registered model descriptors."""
        return list(self._models.values())

    @classmethod
    async def load(cls, db) -> "EmbeddingRegistry":
        """Load all rows from ``embedding_model_registry`` into memory.

        Called once at app startup; requires a service restart to pick up
        registry changes (new models, toggled flags, deprecations).
        """
        rows = await db.fetch(
            "SELECT model_id, table_name, dimension, metric::text,"
            "       accepts_writes, is_queryable"
            " FROM embedding_model_registry"
        )
        models: dict[str, ModelMeta] = {}
        for r in rows:
            metric: str = r["metric"]
            models[r["model_id"]] = ModelMeta(
                model_id=r["model_id"],
                table_name=r["table_name"],
                dimension=r["dimension"],
                metric=metric,  # type: ignore[arg-type]
                accepts_writes=r["accepts_writes"],
                is_queryable=r["is_queryable"],
                operator=_OPERATORS[metric],
            )
        return cls(models)
