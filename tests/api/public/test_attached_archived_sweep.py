"""Contract sweep for the ``attached_archived`` signal (#477).

Four write paths fold anti-resurrection into the ``auto-attached`` disposition —
assignments (#391), events (#322), citations (#319) and assignment relationships
(#301). ``auto-attached`` on its own cannot tell a producer whether it attached to
a live row or to a retracted one, and that ambiguity cost usa-wa a month anchored
to an archived assignment (#474).

These are hermetic shape guards, not behaviour tests (the per-path behaviour lives
in the integration tiers):

1. every wire model that reports a disposition also carries ``attached_archived``;
2. every core result type that can attach to an archived row defaults it to False;
3. every wire construction site that maps a core per-item result **passes it
   through** — an AST ratchet, so a new mapping site that drops the flag fails
   here rather than silently lying to the producer.
"""

import ast
import pathlib

import pytest

from src.api.public.schemas import (
    CitationObservationResult,
    EventObservationResult,
    ObservationResponse,
    RelationshipObservationResult,
)
from src.core.assignment_relationships import RelationshipResult
from src.core.citations import CitationResult
from src.core.observation import AssignmentResolution, EventResult

_PUBLIC_API_DIR = pathlib.Path(__file__).resolve().parents[3] / "src" / "api" / "public"

# The per-item wire models. Each is a pure mapping from a core result dataclass,
# so the flag must be carried at every construction site with no exemptions.
_MAPPED_RESULT_MODELS = (
    "EventObservationResult",
    "CitationObservationResult",
    "RelationshipObservationResult",
)


@pytest.mark.parametrize(
    "model",
    [
        ObservationResponse,
        EventObservationResult,
        CitationObservationResult,
        RelationshipObservationResult,
    ],
    ids=lambda m: m.__name__,
)
def test_wire_model_exposes_attached_archived(model):
    """The signal is additive and optional — absent means "not an archived attach".

    It must never default to False: ``false`` on the wire for every healthy
    observation would be noise, and the existing optional fields on these models
    (``unapplied``, ``reason``) already use None for "not applicable".
    """
    field = model.model_fields.get("attached_archived")
    assert field is not None, f"{model.__name__} does not expose attached_archived (#477)"
    assert field.default is None, f"{model.__name__}.attached_archived must default to None"
    assert model().model_dump().get("attached_archived", "missing") is None


@pytest.mark.parametrize(
    "result_type",
    [AssignmentResolution, EventResult, CitationResult, RelationshipResult],
    ids=lambda t: t.__name__,
)
def test_core_result_defaults_attached_archived_false(result_type):
    """Core results are internal, so the flag is a plain bool defaulting to False."""
    annotations = getattr(result_type, "__annotations__", {})
    assert "attached_archived" in annotations, (
        f"{result_type.__name__} does not carry attached_archived (#477)"
    )
    field = result_type.__dataclass_fields__["attached_archived"]
    assert field.default is False, f"{result_type.__name__}.attached_archived must default to False"


def _construction_sites(model_name: str) -> list[tuple[pathlib.Path, ast.Call]]:
    sites: list[tuple[pathlib.Path, ast.Call]] = []
    for path in sorted(_PUBLIC_API_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == model_name
            ):
                sites.append((path, node))
    return sites


@pytest.mark.parametrize("model_name", _MAPPED_RESULT_MODELS)
def test_every_per_item_result_site_forwards_attached_archived(model_name):
    """AST ratchet, no allowlist: forgetting the flag at a new mapping site fails here."""
    sites = _construction_sites(model_name)
    assert sites, f"no {model_name} construction sites found — has the sweep gone stale?"
    missing = [
        f"{path.name}:{node.lineno}"
        for path, node in sites
        if not any(kw.arg == "attached_archived" for kw in node.keywords)
    ]
    assert not missing, (
        f"{model_name} built without attached_archived= at: {', '.join(missing)} (#477)"
    )
