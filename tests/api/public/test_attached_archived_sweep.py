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
   here rather than silently lying to the producer;
4. ``retract_assignment`` only answers ``AUTO_ATTACHED`` for an archived row —
   the assignments retract branch *infers* the flag from the disposition rather
   than propagating one, so that inference needs pinning.

**Boundary of guard 3:** it covers the three per-item result models, not
``ObservationResponse`` itself, whose ``attached_archived`` is populated only by
the assignments route. Extend it if another surface learns to detect an archived
match — #481 (the identifier lookup that resolves to an archived person or org)
is the likely first.
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
    # And it serialises as null rather than vanishing, so a producer can probe for
    # the key instead of inferring support from its absence.
    dumped = model(disposition="new").model_dump()
    assert "attached_archived" in dumped
    assert dumped["attached_archived"] is None


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


def test_retract_assignment_returns_auto_attached_only_for_an_archived_row():
    """The assignments retract branch infers ``attached_archived`` from the
    disposition (``src/api/public/assignments.py``), unlike the other three paths
    which propagate an explicit flag. That inference is sound only while
    ``retract_assignment`` answers ``AUTO_ATTACHED`` from inside its
    ``archived_at is not None`` guard and nowhere else. A second such return —
    an idempotent no-op for a *live* row, say — would make the wire signal lie
    silently, so pin it here rather than trusting review to catch it.
    """
    tree = ast.parse(pathlib.Path("src/core/observation.py").read_text())
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "retract_assignment"
    )

    def _is_auto_attached(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "AUTO_ATTACHED"
        )

    guarded = {
        id(r)
        for branch in ast.walk(fn)
        if isinstance(branch, ast.If) and "archived_at" in ast.unparse(branch.test)
        for r in ast.walk(branch)
        if _is_auto_attached(r)
    }
    all_returns = [r for r in ast.walk(fn) if _is_auto_attached(r)]

    assert all_returns, "retract_assignment no longer returns AUTO_ATTACHED — has it moved? (#477)"
    unguarded = [r.lineno for r in all_returns if id(r) not in guarded]
    assert not unguarded, (
        f"retract_assignment returns AUTO_ATTACHED outside an archived_at guard at line(s) "
        f"{unguarded} — assignments.py infers attached_archived from this disposition, so that "
        f"return would report a live row as retracted (#477)"
    )
