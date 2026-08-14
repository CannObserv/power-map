"""Source sweeps holding the public wire to `Z`-suffixed timestamps (#440).

`fmt_ts` is unit-tested in `test_schemas.py`, but nothing checked that each site
*uses* it. The failure mode is silent: both `…Z` and `…+00:00` are valid ISO
8601 and every mainstream parser accepts either, so a drifted field ships green
and only surfaces when a consumer diffs two payloads. (Sibling repo `observo`
drifted exactly that way — see its #450 — from the byte-identical convention.)

Two ratchets, in order of load-bearing-ness:

1. **No pre-serialization.** `.isoformat()` outside `fmt_ts` — the only route by
   which a `+00:00` can actually reach the wire, since pydantic's own JSON
   serializer already emits `Z` for a zero-offset aware datetime. A `str`-typed
   `…_at` response field is the same breach declared one layer up.
2. **Every response-model `datetime` field carries a `@field_serializer` calling
   `fmt_ts`.** Redundant with pydantic's default *today*; what it buys is one
   formatter to change when that default moves, and one place to harden if the
   `Z` rule ever needs teeth. It is **not** insurance against a naive or
   non-UTC datetime (CR #440/1): `fmt_ts` only rewrites `+00:00`, so a naive
   value serializes bare and a `+05:00` value keeps its offset — byte-identical
   to pydantic's default in both cases. Request models are exempt — they are
   parsed, not serialized — and are excluded by *resolution* (reachability from
   a `response_model=` declaration) rather than by a name heuristic, which would
   misclassify `CitationObservationItem`.
3. **The published schema matches the field** — the serializer's *return*
   annotation is what OpenAPI shows, so it carries the nullability and the
   `format: date-time` marker (CR #440/4, #440/7).

Both are ratchets, not proofs: indirection through a helper (a module constant,
an f-string, a `str()` call) slips past either, the same caveat
`test_conditional_get.py` documents for its sweeps.
"""

import ast
from functools import cache
from pathlib import Path

from src.api.main import app

PUBLIC_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "public"

# The single sanctioned `.isoformat()` in the public package — every other one
# is the breach this sweep exists to catch.
FORMATTER = "fmt_ts"


@cache
def _public_trees() -> dict[str, ast.Module]:
    """`{filename: parsed module}` for every module in `src/api/public`.

    Cached — every check in this module walks the same 20 files, and the trees
    are read-only (`_isoformat_calls` keys node identity off a live tree, which
    a shared cache keeps alive rather than invalidating).
    """
    return {
        p.name: ast.parse(p.read_text(), filename=str(p)) for p in sorted(PUBLIC_DIR.glob("*.py"))
    }


# ---------------------------------------------------------------------------
# Model resolution — which classes are on the response wire
# ---------------------------------------------------------------------------


def _model_classes(trees: dict[str, ast.Module]) -> dict[str, ast.ClassDef]:
    """`{class name: ClassDef}` for every pydantic model in the public package.

    A class qualifies if it derives from `BaseModel` or from a model already
    seen. Iterated to a fixpoint rather than in one pass (CR #440/2): within a
    module Python guarantees a base is defined before its subclass, but across
    modules the walk order is alphabetical, so a subclass in `assignments.py`
    whose base lives in `schemas.py` would be dropped — and a dropped model is
    swept by nothing, which reads exactly like a pass.
    """
    models: dict[str, ast.ClassDef] = {}
    grew = True
    while grew:
        grew = False
        for tree in trees.values():
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or node.name in models:
                    continue
                bases = {ast.unparse(b) for b in node.bases}
                if "BaseModel" in bases or bases & models.keys():
                    models[node.name] = node
                    grew = True
    return models


def _response_model_names(trees: dict[str, ast.Module]) -> set[str]:
    """Every identifier appearing in a `response_model=` route declaration.

    Unparsed name-wise rather than by value, so a `list[Foo]` or
    `Foo | None` declaration contributes `Foo`.
    """
    roots: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "response_model":
                    roots |= {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
    return roots


def _annotation_names(node: ast.AST) -> set[str]:
    """Bare identifiers inside an annotation — `list[OrgName] | None` → `{list, OrgName}`."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _reachable_models(models: dict[str, ast.ClassDef], roots: set[str]) -> set[str]:
    """Models reachable from a `response_model=` declaration, transitively.

    Follows base classes and field annotations, so a nested item model
    (`OrgSearchResponse.data: list[OrgSearchResult]`) is on the wire too. What
    this excludes is the point: everything left over is a request model, which
    is parsed rather than serialized and carries no `Z` obligation.
    """
    seen: set[str] = set()
    queue = [name for name in roots if name in models]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        node = models[name]
        edges = {ast.unparse(b) for b in node.bases}
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign):
                edges |= _annotation_names(stmt.annotation)
        queue.extend(e for e in edges if e in models and e not in seen)
    return seen


def _inheritance_chain(models: dict[str, ast.ClassDef], name: str) -> list[str]:
    """`name` followed by its model ancestors — fields and serializers both inherit."""
    chain = [name]
    for base in models[name].bases:
        base_name = ast.unparse(base)
        if base_name in models and base_name not in chain:
            chain.extend(c for c in _inheritance_chain(models, base_name) if c not in chain)
    return chain


# ---------------------------------------------------------------------------
# Field / serializer inspection
# ---------------------------------------------------------------------------


def _fields(models: dict[str, ast.ClassDef], name: str) -> dict[str, str]:
    """`{field: annotation}` over the whole inheritance chain, subclass wins."""
    out: dict[str, str] = {}
    for cls in _inheritance_chain(models, name):
        for stmt in models[cls].body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                out.setdefault(stmt.target.id, ast.unparse(stmt.annotation))
    return out


def _fmt_ts_serialized(models: dict[str, ast.ClassDef], name: str) -> set[str]:
    """Field names covered by a `@field_serializer` whose body calls `fmt_ts`.

    A serializer that hand-rolls the same `.replace("+00:00", "Z")` does not
    count — check 1 flags its `.isoformat()` anyway, and single-sourcing the
    formatter is what makes a future change to it reach every field at once.
    """
    covered: set[str] = set()
    for cls in _inheritance_chain(models, name):
        for stmt in models[cls].body:
            if not isinstance(stmt, ast.FunctionDef):
                continue
            targets: set[str] = set()
            for dec in stmt.decorator_list:
                if isinstance(dec, ast.Call) and ast.unparse(dec.func).endswith("field_serializer"):
                    targets |= {
                        a.value
                        for a in dec.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)
                    }
            if not targets:
                continue
            calls_formatter = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == FORMATTER
                for n in ast.walk(stmt)
            )
            if not calls_formatter:
                continue
            # `@field_serializer("*")` covers every field on the model — the
            # concrete one, not the class the decorator sits on (CR #440/5): a
            # wildcard on a base serializes the subclass's own fields too.
            covered |= _fields(models, name).keys() if "*" in targets else targets
    return covered


def _is_datetime(annotation: str) -> bool:
    """True for a `datetime`-typed field. `date` is deliberately excluded — a
    calendar date serializes as `YYYY-MM-DD` and carries no offset to drift."""
    return "datetime" in _annotation_names(ast.parse(annotation, mode="eval").body)


def _is_optional(annotation: str) -> bool:
    """True for a field that can hold `None` — `X | None`, `Optional[X]`."""
    node = ast.parse(annotation, mode="eval").body
    return any(isinstance(n, ast.Constant) and n.value is None for n in ast.walk(node)) or (
        "Optional" in _annotation_names(node)
    )


def _str_aliases(trees: dict[str, ast.Module]) -> frozenset[str]:
    """Module-level names bound to a `str`-ish type, plus `str` itself.

    `TimestampStr = Annotated[str, Field(json_schema_extra=…)]` exists so a
    serializer can publish `format: date-time`; a *field* annotated with it is a
    pre-serialized timestamp under another name, which the bare-name test could
    not see (CR #440/10). Iterated so an alias of an alias resolves. An
    annotation mentioning `datetime` is not string-ish, and `dict[str, X]`-shaped
    aliases would be swept in — none exist, and a false positive here fails
    loudly rather than passing silently.
    """
    aliases = {"str"}
    grew = True
    while grew:
        grew = False
        for tree in trees.values():
            for node in tree.body:
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name) or target.id in aliases:
                    continue
                names = _annotation_names(node.value)
                if names & aliases and "datetime" not in names:
                    aliases.add(target.id)
                    grew = True
    return frozenset(aliases)


def _is_str(annotation: str, aliases: frozenset[str] = frozenset({"str"})) -> bool:
    """True for a `str`-typed field. Written as "mentions a string type, not
    `datetime`" rather than as a subset test (CR #440/3) so `Annotated[str,
    Field(...)]` — the repo's own idiom, cf. `EmbeddingVector` — is caught
    rather than skipped. Pass `_str_aliases(...)` to see through named aliases."""
    names = _annotation_names(ast.parse(annotation, mode="eval").body)
    return bool(names & aliases) and "datetime" not in names


# ---------------------------------------------------------------------------
# Check 1 — nothing pre-serializes a timestamp
# ---------------------------------------------------------------------------


def _isoformat_calls(tree: ast.Module) -> list[int]:
    """Line numbers of `.isoformat()` calls that bypass `fmt_ts`.

    Exempt: the body of `fmt_ts` itself (it *is* the formatter), and
    `<dt>.date().isoformat()`, which yields a `YYYY-MM-DD` calendar date.
    `date.fromisoformat(...)` is parsing, not formatting, and never matches.
    """
    formatter_bodies = {
        id(n)
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef) and f.name == FORMATTER
        for n in ast.walk(f)
    }
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "isoformat" or id(node) in formatter_bodies:
            continue
        receiver = node.func.value
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Attribute)
            and receiver.func.attr == "date"
        ):
            continue
        hits.append(node.lineno)
    return hits


def test_no_module_formats_a_timestamp_outside_fmt_ts():
    """`.isoformat()` is `fmt_ts`'s alone (#440).

    This is the load-bearing half of the rule. Pydantic already emits `Z` for a
    zero-offset aware datetime, so a hand-built string is the only way a
    `+00:00` reaches a consumer — and it bypasses the response model entirely,
    where no `@field_serializer` can catch it.
    """
    offenders = [
        f"{name}:{line}"
        for name, tree in _public_trees().items()
        for line in _isoformat_calls(tree)
    ]
    assert not offenders, f"timestamp formatted outside fmt_ts: {offenders} — call fmt_ts() instead"


def test_no_response_field_declares_a_str_timestamp():
    """A `str`-typed `…_at` field is a pre-serialized timestamp declared up front.

    Nothing then validates the format, and the value was necessarily built by
    hand in a handler — the shape check 1 catches from the other end.
    """
    trees = _public_trees()
    models = _model_classes(trees)
    aliases = _str_aliases(trees)
    offenders = [
        f"{name}.{field}: {annotation}"
        for name in sorted(_reachable_models(models, _response_model_names(trees)))
        for field, annotation in _fields(models, name).items()
        if field.endswith("_at") and _is_str(annotation, aliases)
    ]
    assert not offenders, (
        f"str-typed timestamp on a response model: {offenders} — use datetime + fmt_ts"
    )


# ---------------------------------------------------------------------------
# Check 2 — every response-model datetime field declares its serializer
# ---------------------------------------------------------------------------


def _uncovered_datetime_fields() -> list[str]:
    """`Model.field` for every wire-reachable `datetime` field with no `fmt_ts` serializer."""
    trees = _public_trees()
    models = _model_classes(trees)
    out = []
    for name in sorted(_reachable_models(models, _response_model_names(trees))):
        covered = _fmt_ts_serialized(models, name)
        out.extend(
            f"{name}.{field}"
            for field, annotation in sorted(_fields(models, name).items())
            if _is_datetime(annotation) and field not in covered
        )
    return out


def test_every_response_datetime_field_has_an_fmt_ts_serializer():
    """The explicitness ratchet (#440).

    Decorated and undecorated fields serialize identically under pydantic 2.12,
    which is exactly why a missing decorator rots unnoticed. What the decorator
    buys is a single formatter — one place to change when pydantic's default
    moves, one place to harden if `Z` needs enforcing rather than assuming — and
    that is only worth anything if every field is already routed through it.
    """
    uncovered = _uncovered_datetime_fields()
    assert not uncovered, (
        f"response-model datetime field without an @field_serializer calling fmt_ts: {uncovered}"
    )


def test_sweep_reaches_the_whole_response_surface():
    """Guards against the sweep going vacuous.

    A resolver broken by a refactor (a renamed decorator kwarg, a model moved
    out of `schemas.py`) reports zero offenders and reads exactly like a pass.
    Floors rather than exact counts — new response models land often, and a pin
    that has to be bumped by every unrelated PR gets bumped without being read.
    """
    trees = _public_trees()
    models = _model_classes(trees)
    reachable = _reachable_models(models, _response_model_names(trees))
    checked = [
        (name, field)
        for name in reachable
        for field, annotation in _fields(models, name).items()
        if _is_datetime(annotation)
    ]
    assert len(_response_model_names(trees)) >= 30, "response_model= declarations not being found"
    assert len(reachable) >= 60, f"only {len(reachable)} models reachable from response_model="
    assert len(checked) >= 50, f"only {len(checked)} datetime fields swept"

    # Check 3 skips a model whose component it cannot find and a field absent
    # from `properties` — both silently. If FastAPI's component naming shifts,
    # it would compare nothing and pass (CR #440/11).
    _, verified = _timestamp_property_defects()
    assert verified >= 50, f"check 3 only compared {verified} properties against OpenAPI"


# ---------------------------------------------------------------------------
# Check 3 — what the serializers publish in OpenAPI (CR #440/4, #440/7)
# ---------------------------------------------------------------------------


def _component(model: str, schemas: dict) -> dict | None:
    """The serialization-side component schema for `model`, if the app has one.

    FastAPI splits a model into `-Input`/`-Output` components only when the two
    schemas differ; every model here declares the same fields either way, so the
    bare name is the usual key and `-Output` is the fallback, not the rule.
    """
    return schemas.get(f"{model}-Output") or schemas.get(model)


def _branches(prop: dict) -> list[dict]:
    """The alternatives of a property schema — `anyOf` members, or the schema itself."""
    return prop.get("anyOf", [prop])


def _timestamp_property_defects() -> tuple[list[str], int]:
    """`(defects, properties compared)` for every response timestamp in OpenAPI.

    The count is returned, not just the defects (CR #440/11): both skips below
    are silent, so a component-naming change under a FastAPI bump would compare
    nothing and read as a pass. `test_sweep_reaches_the_whole_response_surface`
    floors it.

    Two ways for the published schema to disagree with the model, both invisible
    from the Python side:

    * **Nullability** — the serializer's return annotation, not the field's, is
      what pydantic publishes. `-> str | None` on a required field advertises a
      null that can never occur; 21 fields shipped that way before CR #440/4.
    * **Missing `format: date-time`** — declaring `-> str` erases the marker the
      `datetime` field would have carried, so a generated client types the field
      as a bare string (CR #440/7). `Annotated[str, Field(json_schema_extra=…)]`
      puts it back.
    """
    schemas = app.openapi()["components"]["schemas"]
    trees = _public_trees()
    models = _model_classes(trees)
    defects: list[str] = []
    compared = 0
    for name in sorted(_reachable_models(models, _response_model_names(trees))):
        component = _component(name, schemas)
        if component is None:  # nested model inlined by FastAPI, nothing to check
            continue
        for field, annotation in sorted(_fields(models, name).items()):
            prop = component.get("properties", {}).get(field)
            if prop is None or not _is_datetime(annotation):
                continue
            compared += 1
            optional = _is_optional(annotation)
            published_null = any(b.get("type") == "null" for b in _branches(prop))
            if published_null != optional:
                defects.append(
                    f"{name}.{field}: field is {'optional' if optional else 'required'} "
                    f"but OpenAPI says {'nullable' if published_null else 'non-nullable'}"
                )
            strings = [b for b in _branches(prop) if b.get("type") == "string"]
            if not strings or any(b.get("format") != "date-time" for b in strings):
                defects.append(f"{name}.{field}: string branch missing format: date-time")
    return defects, compared


def test_published_timestamp_schema_matches_the_field():
    """The wire schema is the serializer's return annotation, not the field (#440).

    Nothing about this is visible while reading `schemas.py` — the field says
    `datetime`, the client sees whatever the serializer promised. Checking it
    against the live `app.openapi()` is the only way the two stay in step.
    """
    defects, _ = _timestamp_property_defects()
    assert not defects, f"published timestamp schema disagrees with the model: {defects}"


def test_isoformat_sweep_detects_a_planted_breach():
    breach = ast.parse(
        'def handler(row):\n    return {"created_at": row["created_at"].isoformat()}\n'
    )
    assert _isoformat_calls(breach) == [2]

    exempt = ast.parse(
        "def fmt_ts(v):\n"
        '    return v.isoformat().replace("+00:00", "Z") if v else None\n'
        "def handler(dt, raw):\n"
        "    born = dt.date().isoformat()\n"
        "    parsed = date.fromisoformat(raw)\n"
        "    return born, parsed\n"
    )
    assert _isoformat_calls(exempt) == []


def test_serializer_sweep_detects_a_planted_breach():
    """A bare `datetime` field, and a serializer that hand-rolls the format."""
    source = (
        "class Bare(BaseModel):\n"
        "    created_at: datetime\n"
        "class HandRolled(BaseModel):\n"
        "    created_at: datetime\n"
        '    @field_serializer("created_at")\n'
        "    def _s(self, v):\n"
        '        return v.isoformat().replace("+00:00", "Z")\n'
        "class Compliant(BaseModel):\n"
        "    created_at: datetime\n"
        "    archived_at: datetime | None = None\n"
        '    @field_serializer("created_at", "archived_at")\n'
        "    def _s(self, v):\n"
        "        return fmt_ts(v)\n"
        "class Inheriting(Compliant):\n"
        "    updated_at: datetime\n"
        '    @field_serializer("updated_at")\n'
        "    def _s2(self, v):\n"
        "        return fmt_ts(v)\n"
    )
    models = _model_classes({"m.py": ast.parse(source)})
    assert _fmt_ts_serialized(models, "Bare") == set()
    assert _fmt_ts_serialized(models, "HandRolled") == set()
    assert _fmt_ts_serialized(models, "Compliant") == {"created_at", "archived_at"}
    # The inherited serializer still covers the inherited field.
    assert _fmt_ts_serialized(models, "Inheriting") == {"created_at", "archived_at", "updated_at"}
    assert set(_fields(models, "Inheriting")) == {"created_at", "archived_at", "updated_at"}


def test_reachability_excludes_request_models():
    """A model reached only from a request body is exempt; a nested response item is not."""
    source = (
        "class Item(BaseModel):\n"
        "    created_at: datetime\n"
        "class Envelope(BaseModel):\n"
        "    data: list[Item]\n"
        "class Body(BaseModel):\n"
        "    recorded_at: datetime\n"
        '@router.post("/x", response_model=Envelope, operation_id="x")\n'
        "async def handler(body: Body):\n"
        "    return body\n"
    )
    trees = {"m.py": ast.parse(source)}
    models = _model_classes(trees)
    assert _reachable_models(models, _response_model_names(trees)) == {"Envelope", "Item"}


def test_str_timestamp_check_ignores_date_fields():
    assert _is_str("str | None")
    assert _is_str("str")
    # CR #440/3 — the repo's own `Annotated[...]` idiom must not slip past.
    assert _is_str("Annotated[str, Field(min_length=1)]")
    assert not _is_str("datetime | None")


def test_str_aliases_resolve_to_the_string_check():
    """CR #440/10 — an alias is a `str` field wearing another name.

    `TimestampStr` exists so serializers can publish `format: date-time`; a
    *field* annotated with it is exactly the pre-serialized timestamp check 1b
    rejects, and the bare-name test could not see it.
    """
    aliases = _str_aliases(_public_trees())
    assert "TimestampStr" in aliases
    # A non-string alias in the same file must not be swept in with it.
    assert "EmbeddingVector" not in aliases
    assert _is_str("TimestampStr", aliases)
    assert _is_str("TimestampStr | None", aliases)
    assert not _is_str("TimestampStr", frozenset({"str"}))  # unresolved: the old blind spot

    # An alias of an alias resolves too.
    chained = {
        "m.py": ast.parse(
            "A = Annotated[str, Field()]\nB = A | None\nC = Annotated[list[float], V]\n"
        )
    }
    assert _str_aliases(chained) >= {"A", "B"}
    assert "C" not in _str_aliases(chained)
    assert _is_datetime("datetime | None")
    assert _is_datetime("Annotated[datetime, Field()]")
    assert not _is_datetime("date | None")


def test_optionality_reads_through_the_annotation_forms():
    assert _is_optional("datetime | None")
    assert _is_optional("Optional[datetime]")
    assert not _is_optional("datetime")
    assert not _is_optional("list[OrgName]")


def test_model_collection_reaches_a_fixpoint_across_modules():
    """CR #440/2 — a subclass whose base sorts later must not be dropped.

    Alphabetical walk order puts `assignments.py` ahead of `schemas.py`, so the
    single-pass form silently lost `Sub` — and a lost model is swept by nothing.
    """
    trees = {
        "assignments.py": ast.parse("class Sub(Base):\n    created_at: datetime\n"),
        "schemas.py": ast.parse("class Base(BaseModel):\n    id: str\n"),
    }
    assert set(_model_classes(trees)) == {"Base", "Sub"}


def test_wildcard_serializer_covers_the_concrete_models_fields():
    """CR #440/5 — a `"*"` serializer on a base also covers subclass fields."""
    models = _model_classes(
        {
            "m.py": ast.parse(
                "class Base(BaseModel):\n"
                "    created_at: datetime\n"
                '    @field_serializer("*")\n'
                "    def _s(self, v):\n"
                "        return fmt_ts(v)\n"
                "class Sub(Base):\n"
                "    updated_at: datetime\n"
            )
        }
    )
    assert _fmt_ts_serialized(models, "Sub") == {"created_at", "updated_at"}
