"""Static assertions for person-name partial templates (Phase 2a Task 2)."""
from pathlib import Path

from src.core.types import PERSON_NAME_TYPES

FORM_ROW = Path("src/templates/admin/people/partials/_name_form_row.html").read_text()
READ_ROW = Path("src/templates/admin/people/partials/_name_row.html").read_text()
DETAIL = Path("src/templates/admin/people/detail.html").read_text()
# Issue #127: metadata fields (visibility / locale / script / sort_as /
# reading_of_id) now live in their own partial, included into both the
# new-name inline form (`_name_form_row.html`) and the existing-row
# Details disclosure (`_name_parts_editor.html`). Static structural
# assertions read the union so the metadata-presence tests work
# regardless of which partial physically owns the markup.
METADATA_FIELDS = Path(
    "src/templates/admin/people/partials/_name_metadata_fields.html"
).read_text()
FORM_ROW_FULL = FORM_ROW + "\n" + METADATA_FIELDS


# ---------------------------------------------------------------------------
# Form row — name_type dropdown driven by src.core.types.PERSON_NAME_TYPES
# ---------------------------------------------------------------------------


def test_form_row_renders_an_option_for_every_person_name_type():
    """Rendered dropdown must expose every value the schema CHECK allows.

    The template iterates `name_types` from the route context; this test
    drives the actual Jinja render with PERSON_NAME_TYPES so a future
    schema expansion (caught by tests/core/test_types.py) propagates
    here automatically.
    """
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    rendered = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=None, person_id="p-test", name_types=PERSON_NAME_TYPES)
    for t in PERSON_NAME_TYPES:
        assert f'<option value="{t}"' in rendered, (
            f"name_type option {t!r} missing from rendered form row"
        )


def test_form_row_does_not_hardcode_name_type_list():
    """Guard against re-introducing a hardcoded literal list.

    Two structural assertions, tighter than a loose word-match:

    1. The template must iterate the route-provided ``name_types``
       context var (``{% for t in name_types %}``).
    2. The ``<select name="name_type">`` block must contain no
       quoted-literal ``name_type`` value — that would mean someone
       reintroduced the hardcoded tuple alongside (or instead of) the
       loop.
    """
    assert "{% for t in name_types %}" in FORM_ROW, (
        "form row template must iterate `name_types` from context, "
        "not a hardcoded literal"
    )
    # Slice the dropdown block: from `<select name="name_type"` to its
    # closing `</select>`. Any quoted literal `name_type` token inside
    # (e.g. `'legal'`, `"alias"`) is the regression we're guarding.
    select_open = FORM_ROW.index('<select name="name_type"')
    select_close = FORM_ROW.index("</select>", select_open)
    block = FORM_ROW[select_open:select_close]
    for t in PERSON_NAME_TYPES:
        assert f"'{t}'" not in block, (
            f"name_type dropdown block contains hardcoded literal {t!r}"
        )
        assert f'"{t}"' not in block, (
            f"name_type dropdown block contains hardcoded literal {t!r}"
        )


def test_form_row_name_type_select_is_named_correctly():
    assert 'name="name_type"' in FORM_ROW


# ---------------------------------------------------------------------------
# Form row — visibility select
# ---------------------------------------------------------------------------

VISIBILITY_VALUES = ("public", "legal_only", "hidden")


def test_form_row_has_visibility_select():
    assert 'name="visibility"' in FORM_ROW_FULL


def test_form_row_offers_all_three_visibility_values():
    for v in VISIBILITY_VALUES:
        assert f"'{v}'" in FORM_ROW_FULL, f"visibility option {v!r} missing"


def test_form_row_visibility_select_has_label():
    """A11y: the visibility control must be labelled."""
    # Either a <label> element, an aria-label, or aria-labelledby reference.
    has_label = (
        'aria-label="Visibility"' in FORM_ROW_FULL
        or "<label" in FORM_ROW_FULL.split('name="visibility"')[0][-200:]
    )
    assert has_label, "visibility select needs a label or aria-label"


def test_form_row_visibility_defaults_to_public_when_no_existing_row():
    """For new rows (n is None), the public <option> must end up selected.

    The template uses `(not n and v == 'public')` in the option's selected
    expression — verify both the structural pattern and the runtime result.
    """
    # Structural check: the Jinja guard must reference (not n) and 'public'.
    assert (
        "not n and v == 'public'" in FORM_ROW_FULL
        or "v == 'public'" in FORM_ROW_FULL
    )

    # Runtime check: render with n=None and confirm <option value="public" selected>.
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    rendered = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=None, person_id="p-test")
    assert '<option value="public" selected>' in rendered, (
        "public should be the pre-selected visibility for new rows"
    )


# ---------------------------------------------------------------------------
# Read row — visibility badge for non-public rows
# ---------------------------------------------------------------------------


def test_read_row_renders_visibility_badge_for_non_public():
    """Non-public rows must render a visibility badge so admins can see the state."""
    # The template should branch on n.visibility != 'public' and render a badge.
    assert "visibility" in READ_ROW
    assert "n.visibility" in READ_ROW or "visibility !=" in READ_ROW


# ---------------------------------------------------------------------------
# Detail page — deadname-confirm script wired
# ---------------------------------------------------------------------------


def test_detail_loads_deadname_confirm_script():
    """Phase 2a Task 4: deadname-confirm JS must be served on person detail."""
    assert "person-name-deadname-confirm.js" in DETAIL


def test_detail_deadname_confirm_script_is_deferred():
    """Defer ensures the script runs after DOM parse — required for safe scan."""
    # Allow versioned src; the defer attr should appear on the same script tag.
    block = DETAIL.split("person-name-deadname-confirm.js")[1].split(">")[0]
    assert "defer" in block


# ---------------------------------------------------------------------------
# Form row — locale / script typeahead inputs + sort_as plain input (Phase 2b)
# ---------------------------------------------------------------------------


def test_form_row_has_locale_typeahead_combobox():
    """Locale input wired to /admin/people/_locale_search with combobox a11y."""
    assert 'name="locale"' in FORM_ROW_FULL
    assert "/admin/people/_locale_search" in FORM_ROW_FULL
    assert 'role="combobox"' in FORM_ROW_FULL
    # Issue #131: aria-controls now namespaced; allow any suffix.
    assert 'aria-controls="locale-search-results-' in FORM_ROW_FULL
    assert 'aria-haspopup="listbox"' in FORM_ROW_FULL


def test_form_row_locale_results_listbox_present():
    assert 'id="locale-search-results-' in FORM_ROW_FULL
    assert 'role="listbox"' in FORM_ROW_FULL


def test_form_row_has_script_typeahead_combobox():
    assert 'name="script"' in FORM_ROW_FULL
    assert "/admin/people/_script_search" in FORM_ROW_FULL
    assert 'aria-controls="script-search-results-' in FORM_ROW_FULL


def test_form_row_script_results_listbox_present():
    assert 'id="script-search-results-' in FORM_ROW_FULL


def test_form_row_has_sort_as_plain_input():
    """sort_as is a plain text input — not a combobox."""
    assert 'name="sort_as"' in FORM_ROW_FULL


ROW_TYPEAHEAD_JS = Path(
    "src/static/admin/person-name-row-typeahead.js"
).read_text()


def test_form_row_calls_init_typeahead_for_locale_and_script():
    """Issue #131: typeahead init was extracted to
    `person-name-row-typeahead.js`. The form row marks itself for
    discovery via `data-name-row-typeahead` + `data-uid`; the external
    module wires locale + script via window.initTypeaheadCombobox(...)."""
    assert "data-name-row-typeahead" in FORM_ROW
    assert ROW_TYPEAHEAD_JS.count("initTypeaheadCombobox") >= 2
    assert "locale-search-display-" in ROW_TYPEAHEAD_JS
    assert "script-search-display-" in ROW_TYPEAHEAD_JS


# ---------------------------------------------------------------------------
# Read row — subtitle line surfaces locale / script / sort_as when set
# ---------------------------------------------------------------------------


def test_read_row_references_metadata_columns():
    assert "n.locale" in READ_ROW
    assert "n.script" in READ_ROW
    assert "n.sort_as" in READ_ROW


def test_read_row_subtitle_skips_when_all_metadata_null():
    """No subtitle markers when all three fields are NULL."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    bare = {
        "id": "n1", "name": "Plain", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=bare, person_id="p1")
    assert "·" not in out
    assert "sort_as:" not in out


def test_read_row_subtitle_renders_locale_and_script():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n1", "name": "Test", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": "en-US", "script": "Latn", "sort_as": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "Latn" in out
    assert "en-US" in out


def test_read_row_subtitle_renders_sort_as():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n1", "name": "van der Meer", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": "Meer, van der",
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "Meer, van der" in out
    assert "sort_as" in out.lower()


# ---------------------------------------------------------------------------
# Form row — reading_of_id typeahead (Phase 2c)
# ---------------------------------------------------------------------------


def test_form_row_has_reading_of_id_typeahead():
    assert 'name="reading_of_id"' in FORM_ROW_FULL
    assert "_reading_target_search" in FORM_ROW_FULL
    assert 'aria-controls="reading-of-results-' in FORM_ROW_FULL
    assert 'aria-haspopup="listbox"' in FORM_ROW_FULL


def test_form_row_reading_of_results_listbox_present():
    assert 'id="reading-of-results-' in FORM_ROW_FULL


def test_form_row_calls_init_typeahead_for_reading_of():
    """Three combobox factories now: locale + script + reading_of.
    Wiring lives in `person-name-row-typeahead.js` (#131 extraction)."""
    assert ROW_TYPEAHEAD_JS.count("initTypeaheadCombobox") >= 3
    assert "reading-of-display-" in ROW_TYPEAHEAD_JS


def test_form_row_reading_of_block_is_conditional():
    """Block must be wrapped so JS can show/hide based on name_type."""
    # Either a wrapping element with id (now namespaced — any suffix), or
    # a class hook that the JS toggles.
    assert (
        'id="reading-of-block-' in FORM_ROW_FULL
        or "data-reading-of-block" in FORM_ROW_FULL
    )


def test_form_row_has_reading_type_toggle_script():
    """JS must show the block when name_type ∈ {reading, romanization, mrz}.

    Issue #131: the toggle JS was extracted from inline `<script>` to
    `person-name-row-typeahead.js`. Issue #135: the form row dropdown
    no longer carries literal name_type strings (driven by
    `PERSON_NAME_TYPES`), so this assertion now lives where the actual
    matching is performed — the external JS module.
    """
    assert "'reading'" in ROW_TYPEAHEAD_JS
    assert "'romanization'" in ROW_TYPEAHEAD_JS
    assert "'mrz'" in ROW_TYPEAHEAD_JS


# ---------------------------------------------------------------------------
# Read row — linked-name subtitle (Phase 2c)
# ---------------------------------------------------------------------------


def test_read_row_references_reading_of_id():
    assert "n.reading_of_id" in READ_ROW or "reading_of_name" in READ_ROW


def test_read_row_renders_reading_of_subtitle_when_set():
    """Linked rows render '↳ {name_type} of: <parent name>' under the name."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n_reading", "name": "ada lovelace", "name_type": "romanization",
        "is_canonical": False, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
        "reading_of_id": "n_legal", "reading_of_name": "Ada Lovelace",
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "↳" in out
    assert "Ada Lovelace" in out
    assert "romanization" in out


def test_read_row_skips_reading_of_subtitle_when_unlinked():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n1", "name": "Plain", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
        "reading_of_id": None, "reading_of_name": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "↳" not in out


# ---------------------------------------------------------------------------
# Delete confirm — cascade hint when child rows exist (Phase 2c)
# ---------------------------------------------------------------------------


def test_read_row_delete_confirm_mentions_cascade_when_children():
    """When the row has reading_of children, the delete confirm should warn."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n_legal", "name": "Ada Lovelace", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
        "reading_of_id": None, "reading_of_name": None,
        "reading_child_count": 2,
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    # Expect the hx-confirm to mention the cascade impact (count + word).
    assert "2" in out and ("linked" in out.lower() or "cascade" in out.lower())


def test_read_row_delete_confirm_default_when_no_children():
    """No children → standard confirm text, no cascade noise."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n_legal", "name": "Plain", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
        "reading_of_id": None, "reading_of_name": None,
        "reading_child_count": 0,
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "Delete this name?" in out


def test_read_row_badge_uses_badge_class():
    """Visibility badge should use the existing badge component for visual consistency."""
    # Find the visibility-conditional block and confirm 'badge' appears within it.
    if "n.visibility" in READ_ROW:
        # Take a window after the first n.visibility ref
        idx = READ_ROW.find("n.visibility")
        window = READ_ROW[idx:idx + 300]
        assert "badge" in window, "visibility branch should render a badge element"


# ---------------------------------------------------------------------------
# Form row — structured parts editor (Phase 2d)
# ---------------------------------------------------------------------------


PARTS_EDITOR = Path(
    "src/templates/admin/people/partials/_name_parts_editor.html"
).read_text()


def test_form_row_includes_parts_editor_partial():
    """Form row template must include the parts editor partial."""
    assert "_name_parts_editor.html" in FORM_ROW


def test_parts_editor_renders_only_when_editing_existing_row():
    """The editor block is gated on `n` being non-None (no name_id → no upsert)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n=None, parts=None, person_id="p1")
    assert out.strip() == "" or "<form" not in out


def test_parts_editor_has_no_inner_form():
    """Issue #127: the Details body is markup nested in the parent name form;
    no inner <form> element."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "<form" not in out


def test_parts_editor_has_no_save_parts_button():
    """Issue #127: the parent form's single Save covers parts too."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "Save parts" not in out


def test_parts_editor_has_no_remove_button_even_when_parts_exist():
    """Issue #127: clearing all fields + Save deletes the row; explicit
    Remove button is removed."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(
        n={"id": "nid_x"},
        parts={
            "given_names": ["Ada"], "family_names": None,
            "additional_names": None, "honorific_prefix": None,
            "honorific_suffix": None, "primary_identifier": "given",
        },
        person_id="pid_x",
    )
    assert "Remove structured parts" not in out
    assert "Remove Details" not in out


def test_parts_editor_offers_all_four_primary_identifiers():
    """The dropdown must mirror the DB CHECK: family/given/patronymic/mononym."""
    for v in ("family", "given", "patronymic", "mononym"):
        assert f"'{v}'" in PARTS_EDITOR or f'"{v}"' in PARTS_EDITOR, v


def test_parts_editor_renders_cardstack_for_each_array_field():
    """Issue #127: arrays render as a vertical card stack.
    Each existing value gets one card (one input + remove button); a single
    Add button per field appends new cards up to the 5-cap. Empty arrays
    render zero cards (Add button only).
    """
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    parts = {
        "given_names": ["María", "José"],
        "family_names": ["García"],
        "additional_names": [],
        "honorific_prefix": None,
        "honorific_suffix": None,
        "primary_identifier": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=parts, person_id="pid_x")
    # Each card carries a data-cardstack-card="<field>" hook for the JS.
    assert out.count('data-cardstack-card="given_names"') == 2
    assert out.count('data-cardstack-card="family_names"') == 1
    assert out.count('data-cardstack-card="additional_names"') == 0
    # One Add button per field (always present, even when array empty).
    assert out.count('data-cardstack-add="given_names"') == 1
    assert out.count('data-cardstack-add="family_names"') == 1
    assert out.count('data-cardstack-add="additional_names"') == 1
    # Stack hook for the JS to find the cards container.
    assert 'data-cardstack="given_names"' in out
    assert 'data-cardstack="family_names"' in out
    assert 'data-cardstack="additional_names"' in out


def test_parts_editor_drops_max_5_hint():
    """Issue #127: '(max 5)' hint removed; cap surfaced via disabled Add button."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "max 5" not in out


def test_parts_editor_pre_populates_arrays():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    parts = {
        "given_names": ["María", "José"],
        "family_names": ["García", "López"],
        "additional_names": None,
        "honorific_prefix": "Dra.",
        "honorific_suffix": None,
        "primary_identifier": "family",
    }
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=parts, person_id="pid_x")
    assert 'value="María"' in out
    assert 'value="José"' in out
    assert 'value="García"' in out
    assert 'value="López"' in out
    assert 'value="Dra."' in out
    # primary_identifier=family should be marked selected
    assert ('value="family" selected' in out) or ('selected>family' in out)


def test_parts_editor_details_has_stable_id_for_future_swaps():
    """The <details> element exposes a stable id so a future feature
    that refreshes the entire editor body after save has an anchor."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert 'id="parts-editor-nid_x"' in out


def test_parts_editor_summary_has_stable_id_for_oob_swap():
    """The <summary> exposes a stable id so the upsert/delete handlers
    can swap just the badge via hx-swap-oob without collapsing the
    user's open <details>."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert 'id="parts-summary-nid_x"' in out


def test_parts_editor_summary_label_says_details():
    """Issue #127: rename 'Structured parts' to 'Details' (UI label only)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "Details" in out
    assert "Structured parts" not in out


# ---------------------------------------------------------------------------
# Read row — parts subtitle (Phase 2d)
# ---------------------------------------------------------------------------


def test_read_row_renders_parts_subtitle_when_present():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n1", "name": "María José García López", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
        "reading_of_id": None, "reading_of_name": None,
        "parts_summary": "García López · María José",
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "García López" in out
    assert "parts:" not in out  # prefix dropped by #127


def test_read_row_skips_parts_subtitle_when_absent():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    row = {
        "id": "n1", "name": "Plain", "name_type": "legal",
        "is_canonical": True, "visibility": "public",
        "locale": None, "script": None, "sort_as": None,
        "reading_of_id": None, "reading_of_name": None,
        "parts_summary": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_row.html"
    ).render(n=row, person_id="p1")
    assert "parts:" not in out


# ---------------------------------------------------------------------------
# Detail page — CardStack JS wiring (Issue #127 Task B)
# ---------------------------------------------------------------------------


def test_detail_loads_parts_cardstack_script():
    """Issue #127: detail page loads the CardStack JS."""
    from pathlib import Path
    DETAIL = Path("src/templates/admin/people/detail.html").read_text()
    assert "person-name-parts-cardstack.js" in DETAIL


def test_detail_parts_cardstack_script_is_deferred():
    """Cache-bust suffix `?v=1` matches the deadname-confirm convention."""
    from pathlib import Path
    DETAIL = Path("src/templates/admin/people/detail.html").read_text()
    assert (
        'src="/static/admin/person-name-parts-cardstack.js?v=1" defer'
        in DETAIL
    )


# ---------------------------------------------------------------------------
# Parts editor — labels + help text (Issue #127 Task C)
# ---------------------------------------------------------------------------


def test_parts_editor_renders_labels_and_help_text():
    """Issue #127: every Details field has a clear label and one-line help."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    expected_labels = [
        "Primary identifier", "Given names", "Family names",
        "Additional names", "Honorific prefix", "Honorific suffix",
    ]
    for label in expected_labels:
        assert label in out, f"missing label: {label!r}"
    # Help text fragments — one distinctive substring per field.
    for help_substring in (
        "primary surname-equivalent",
        "Order matters",
        "Surnames or clan",
        "Middle names",
        "Title that precedes",
        "Suffix that follows",
    ):
        assert help_substring in out, f"missing help: {help_substring!r}"


# ---------------------------------------------------------------------------
# Form row — unified Details disclosure (Issue #127 Task E)
# ---------------------------------------------------------------------------


def test_inline_row_excludes_metadata_fields():
    """Issue #127 bullet 1: name/type/canonical/Save/Cancel inline only.

    For an existing-row edit, the metadata fields (visibility, locale, script,
    sort_as, reading_of_id) must live inside the Details disclosure, not on
    the inline row.
    """
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n={"id": "nid_x", "name": "X", "name_type": "legal",
                "is_canonical": True, "visibility": "public",
                "locale": None, "script": None, "sort_as": None,
                "reading_of_id": None, "reading_of_name": None},
             parts=None, person_id="pid_x")
    inline_section = out.split("<details", 1)[0]
    for needle in ('name="visibility"', 'name="locale"', 'name="script"',
                   'name="sort_as"', 'name="reading_of_id"'):
        assert needle not in inline_section, f"inline row leaks {needle!r}"
    details_section = out[out.index("<details"):]
    for needle in ('name="visibility"', 'name="locale"', 'name="script"',
                   'name="sort_as"', 'name="reading_of_id"'):
        assert needle in details_section, f"Details missing {needle!r}"


def test_disclosure_auto_opens_when_metadata_set():
    """Issue #127: auto-open Details when any non-default metadata present."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    n_with_locale = {"id": "nid_x", "name": "X", "name_type": "legal",
                     "is_canonical": True, "visibility": "public",
                     "locale": "ja-JP", "script": None, "sort_as": None,
                     "reading_of_id": None, "reading_of_name": None}
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=n_with_locale, parts=None, person_id="pid_x")
    import re
    assert re.search(r"<details[^>]*\bopen\b", out)


def test_disclosure_closed_for_pristine_row():
    """No metadata set, no parts → Details closed by default."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    n = {"id": "nid_x", "name": "X", "name_type": "legal",
         "is_canonical": True, "visibility": "public",
         "locale": None, "script": None, "sort_as": None,
         "reading_of_id": None, "reading_of_name": None}
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=n, parts=None, person_id="pid_x")
    import re
    assert not re.search(r"<details[^>]*\bopen\b", out)


# ---------------------------------------------------------------------------
# Typeahead inputs — must send `q` to the search endpoints (Issue #131)
# ---------------------------------------------------------------------------
#
# Bug: search inputs were named `q_locale` / `q_script` / `q_reading_of`,
# so HTMX form-serialised the trio and `hx-params="q"` filtered everything
# out (no input was named `q`). Endpoints received an empty `q` and
# returned zero rows. The lookups appeared broken.
#
# Fix shape: the search-only inputs must NOT be form-named (they should
# stay out of the parent Save POST), and the typeahead request must send
# `q={value}`. We assert via `hx-vals` carrying a `q:` JS expression and
# the absence of the old `name="q_..."` attributes.


def test_locale_input_sends_q_to_search_endpoint():
    """Locale typeahead must send the input value as `q`."""
    # The old buggy form had name="q_locale". The fix uses hx-vals to map
    # the input value to `q`.
    assert 'name="q_locale"' not in METADATA_FIELDS, (
        "locale input must not carry name='q_locale' — it pollutes Save POST"
        " and the server endpoint expects param `q`"
    )
    # hx-vals must populate q from the input element value.
    assert "hx-vals=" in METADATA_FIELDS
    locale_block = METADATA_FIELDS.split("/admin/people/_locale_search")[1]
    locale_block = locale_block.split("</div>")[0]
    assert "hx-vals" in locale_block, "locale typeahead missing hx-vals"
    assert "q:" in locale_block or "'q'" in locale_block or '"q"' in locale_block, (
        "locale typeahead hx-vals must declare a `q` key"
    )


def test_script_input_sends_q_to_search_endpoint():
    assert 'name="q_script"' not in METADATA_FIELDS, (
        "script input must not carry name='q_script'"
    )
    script_block = METADATA_FIELDS.split("/admin/people/_script_search")[1]
    script_block = script_block.split("</div>")[0]
    assert "hx-vals" in script_block, "script typeahead missing hx-vals"
    assert "q:" in script_block or "'q'" in script_block or '"q"' in script_block


def test_reading_of_input_sends_q_to_search_endpoint():
    assert 'name="q_reading_of"' not in METADATA_FIELDS, (
        "reading-of input must not carry name='q_reading_of'"
    )
    reading_block = METADATA_FIELDS.split("_reading_target_search")[1]
    reading_block = reading_block.split("</div>")[0]
    assert "hx-vals" in reading_block, "reading-of typeahead missing hx-vals"
    assert (
        "q:" in reading_block or "'q'" in reading_block or '"q"' in reading_block
    )


def test_typeahead_inputs_drop_hx_params_filter():
    """`hx-params="q"` was dropping everything because no input was named
    `q`. With hx-vals carrying `q`, the filter is no longer needed."""
    assert 'hx-params="q"' not in METADATA_FIELDS, (
        "remove hx-params='q' once hx-vals supplies q directly"
    )


# ---------------------------------------------------------------------------
# Typeahead element IDs — must be unique per name row (Issue #131)
# ---------------------------------------------------------------------------
#
# Latent bug exposed by the q-param fix: when a user has an Edit drawer
# open AND clicks "+ Add name" (afterbegin swap), two sets of inputs
# share the same hard-coded IDs (`locale-search-display`, etc.).
# `getElementById` returns the first match, leaving one of the two
# typeaheads unwired and HTMX `hx-target="#locale-search-results"`
# pointing at the wrong listbox.
#
# Fix: namespace element IDs by the name row's id (`n.id`) for existing
# rows, or `new` for the inline new-name form.


def _render_metadata(n_id):
    """Helper: render the metadata partial for either an existing row or
    the new-name form (n=None) and return the HTML."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    if n_id is None:
        n = None
    else:
        n = {
            "id": n_id, "name": "X", "name_type": "legal",
            "is_canonical": True, "visibility": "public",
            "locale": None, "script": None, "sort_as": None,
            "reading_of_id": None, "reading_of_name": None,
        }
    return env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=n, parts=None, person_id="pid_x")


def test_typeahead_ids_namespaced_per_existing_row():
    """Existing-row drawer must use IDs suffixed with the row's id."""
    out = _render_metadata("nid_abc")
    # The display input, listbox, and hidden field all carry the suffix.
    for stem in (
        "locale-search-display", "locale-search-results", "locale-hidden",
        "script-search-display", "script-search-results", "script-hidden",
        "reading-of-display", "reading-of-results", "reading-of-hidden",
        "reading-of-block",
    ):
        assert f'id="{stem}-nid_abc"' in out, (
            f"expected {stem!r} id namespaced to row id, got plain stem"
        )


def test_typeahead_ids_use_new_suffix_for_new_name_form():
    """The new-name form (n is None) must use a deterministic non-row
    suffix so it doesn't collide with any existing-row drawer."""
    out = _render_metadata(None)
    for stem in (
        "locale-search-display", "locale-search-results", "locale-hidden",
        "script-search-display", "script-search-results", "script-hidden",
    ):
        assert f'id="{stem}-new"' in out, (
            f"expected {stem!r} id suffixed with -new for the new-name form"
        )


def test_aria_controls_match_namespaced_listbox_ids():
    """Inputs reference the namespaced listbox via aria-controls."""
    out = _render_metadata("nid_abc")
    assert 'aria-controls="locale-search-results-nid_abc"' in out
    assert 'aria-controls="script-search-results-nid_abc"' in out
    assert 'aria-controls="reading-of-results-nid_abc"' in out


def test_hx_target_uses_namespaced_listbox_ids():
    """HTMX must point at the row's own listbox, not a global ID."""
    out = _render_metadata("nid_abc")
    assert 'hx-target="#locale-search-results-nid_abc"' in out
    assert 'hx-target="#script-search-results-nid_abc"' in out
    assert 'hx-target="#reading-of-results-nid_abc"' in out


def test_form_row_carries_uid_for_external_typeahead_init():
    """Issue #131: typeahead init was extracted to a static .js file. The
    form row carries `data-name-row-typeahead` + `data-uid="<uid>"` so
    the external module can discover the row and wire its three
    typeaheads with the row-specific IDs."""
    out = _render_metadata("nid_abc")
    assert "data-name-row-typeahead" in out
    assert 'data-uid="nid_abc"' in out


def test_external_typeahead_module_uses_namespaced_ids():
    """The extracted module composes element ids by concatenating the
    stem (e.g. `locale-search-display-`) with the row's uid."""
    # All three typeaheads wired by id-stem prefix.
    for stem in (
        "locale-search-display-",
        "script-search-display-",
        "reading-of-display-",
    ):
        assert stem in ROW_TYPEAHEAD_JS, f"stem {stem!r} missing from external module"


def test_reading_of_block_toggle_uses_namespaced_id():
    """The visibility-toggle reads the namespaced reading-of block id."""
    assert "'reading-of-block-' + uid" in ROW_TYPEAHEAD_JS


# ---------------------------------------------------------------------------
# Metadata field order — Visibility, Sort As / Locale, Script (Issue #131)
# ---------------------------------------------------------------------------
#
# The issue asks for two visual rows: (Visibility, Sort As) above
# (Locale, Script). Markup-wise that means the four `<div class=
# "form-group">` blocks render in this order:
#   1. visibility
#   2. sort_as
#   3. locale
#   4. script


def test_metadata_field_order_visibility_then_sort_as_then_locale_then_script():
    """Pluck the input-name occurrences and assert ordering."""
    fields = ("visibility", "sort_as", "locale", "script")
    positions = {}
    for f in fields:
        # `name="<f>"` for both <input> and <select> — first occurrence is
        # the field's primary form control. For locale/script the first
        # occurrence is the hidden input; for sort_as / visibility it's
        # the visible control. All occur in source order, so the relative
        # ordering is what matters.
        positions[f] = METADATA_FIELDS.index(f'name="{f}"')
    assert (
        positions["visibility"]
        < positions["sort_as"]
        < positions["locale"]
        < positions["script"]
    ), f"metadata field order wrong: {positions}"


# ---------------------------------------------------------------------------
# Placeholders carry usage hints (Issue #131)
# ---------------------------------------------------------------------------


def test_locale_label_carries_bcp47_parenthetical():
    """Locale label is `Locale (BCP-47)` — the standard moves from
    placeholder into the label so the placeholder reads as an example
    rather than dual-purpose."""
    assert ">Locale (BCP-47)" in METADATA_FIELDS, (
        "locale label must read 'Locale (BCP-47)'"
    )


def test_locale_placeholder_carries_example_codes_only():
    """Locale placeholder shows example codes only — no `BCP 47` text
    (that moved to the label)."""
    locale_block = METADATA_FIELDS.split('id="locale-search-display')[1]
    locale_block = locale_block.split("</label>")[0]
    assert "placeholder=" in locale_block
    placeholder = locale_block.split('placeholder="')[1].split('"')[0]
    assert any(code in placeholder for code in ("en-US", "ja-JP", "en, ")), (
        f"locale placeholder lacks example codes: {placeholder!r}"
    )
    # The standard label is now in the field's `<label>`, not the placeholder.
    assert "BCP" not in placeholder, (
        f"locale placeholder should not duplicate the BCP-47 label: {placeholder!r}"
    )


def test_script_label_carries_iso15924_parenthetical():
    """Script label is `Script (ISO 15924)`."""
    assert ">Script (ISO 15924)" in METADATA_FIELDS, (
        "script label must read 'Script (ISO 15924)'"
    )


def test_script_placeholder_carries_example_codes_only():
    """Script placeholder shows example codes only — no `ISO 15924` text."""
    script_block = METADATA_FIELDS.split('id="script-search-display')[1]
    script_block = script_block.split("</label>")[0]
    placeholder = script_block.split('placeholder="')[1].split('"')[0]
    assert any(code in placeholder for code in ("Latn", "Jpan", "Cyrl", "Hans")), (
        f"script placeholder lacks example codes: {placeholder!r}"
    )
    assert "ISO" not in placeholder, (
        f"script placeholder should not duplicate the ISO 15924 label: {placeholder!r}"
    )


def test_sort_as_label_carries_comma_separated_parenthetical():
    """Sort As label is `Sort as (comma separated)` — guidance for
    multi-token collation keys."""
    assert ">Sort as (comma separated)" in METADATA_FIELDS, (
        "sort_as label must read 'Sort as (comma separated)'"
    )


def test_sort_as_placeholder_describes_purpose():
    """Sort As placeholder still describes what to put there."""
    sort_as_block = METADATA_FIELDS.split('name="sort_as"')[1]
    sort_as_block = sort_as_block.split(">")[0]
    placeholder = sort_as_block.split('placeholder="')[1].split('"')[0]
    assert (
        "Smith" in placeholder
        or "surname" in placeholder.lower()
        or "last name" in placeholder.lower()
        or "," in placeholder
    ), f"sort_as placeholder is uninformative: {placeholder!r}"
    assert placeholder != "Sort as (optional)"


def _honorific_block(field):
    """Slice the parts-editor template down to the form-group containing
    the named honorific input, so we can inspect just that block."""
    PARTS = Path(
        "src/templates/admin/people/partials/_name_parts_editor.html"
    ).read_text()
    # Walk back from the input's name attr to the enclosing form-group div.
    input_idx = PARTS.index(f'name="{field}"')
    group_open = PARTS.rfind('<div class="form-group"', 0, input_idx)
    # Find the matching </div>: form-group is structurally simple (label
    # + input only after the redesign), so the next </div> closes it.
    group_close = PARTS.index("</div>", input_idx)
    return PARTS[group_open:group_close + len("</div>")]


def test_honorific_prefix_placeholder_carries_examples_and_drops_small_help():
    """Honorific prefix: examples in placeholder; <small> below removed."""
    block = _honorific_block("honorific_prefix")
    placeholder = block.split('placeholder="')[1].split('"')[0]
    assert any(ex in placeholder for ex in ("Dr.", "Hon.", "Sir")), (
        f"honorific_prefix placeholder lacks examples: {placeholder!r}"
    )
    # The below-control <small> helper is gone (placeholder absorbs the hint).
    assert "<small" not in block, (
        "honorific_prefix block still has a <small> helper element"
    )


def test_honorific_suffix_placeholder_carries_examples_and_drops_small_help():
    block = _honorific_block("honorific_suffix")
    placeholder = block.split('placeholder="')[1].split('"')[0]
    assert any(ex in placeholder for ex in ("Jr.", "PhD", "II")), (
        f"honorific_suffix placeholder lacks examples: {placeholder!r}"
    )
    assert "<small" not in block, (
        "honorific_suffix block still has a <small> helper element"
    )


# ---------------------------------------------------------------------------
# Primary Identifier — help text above the control (Issue #131)
# ---------------------------------------------------------------------------


def test_primary_identifier_help_text_above_control():
    """Help text appears between the label and the <select>, not below it."""
    PARTS = Path(
        "src/templates/admin/people/partials/_name_parts_editor.html"
    ).read_text()
    # Find the primary_identifier select and check the help text precedes it.
    sel_idx = PARTS.index('name="primary_identifier"')
    # Grab a window before the select.
    before = PARTS[max(0, sel_idx - 800):sel_idx]
    after = PARTS[sel_idx:sel_idx + 800]
    # The distinctive help substring must appear BEFORE the select, not after.
    needle = "primary surname-equivalent"
    assert needle in before, (
        "primary_identifier help text should appear above the control"
    )
    assert needle not in after, (
        "primary_identifier help text should NOT appear after the control too"
    )


# ---------------------------------------------------------------------------
# Given / Family / Additional inputs — full-size styling (Issue #131)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# + Add name button — disable while an unsaved new-name row is present (#131)
# ---------------------------------------------------------------------------
#
# CR follow-up: clicking + Add name twice without saving prepends a second
# `<tr id="name-row-new">`, reintroducing the same id-collision class the
# per-row namespacing was meant to prevent (both rows would share the
# `_uid="new"` suffix). Disable the button while a new-row exists; re-enable
# on Save (htmx:afterSwap on the table) or Cancel (the new-name form's
# inline onclick dispatches the powerMap:newNameRowClosed event).
#
# Round-2 CR: extracted from inline `<script>` into
# `src/static/admin/person-detail-add-name-guard.js` so the listener can
# scope to `#names-table` (avoiding unrelated body-level swaps) and so a
# CSP-tightening pass doesn't have to special-case the inline script.


ADD_NAME_GUARD_JS = Path(
    "src/static/admin/person-detail-add-name-guard.js"
).read_text()


def test_detail_add_name_button_has_id():
    """Stable id needed so the guard script can find the button."""
    assert 'id="add-name-btn"' in DETAIL


def test_detail_loads_add_name_guard_script():
    """Detail page must load the extracted +Add guard script."""
    assert "person-detail-add-name-guard.js" in DETAIL


def test_add_name_guard_script_uses_names_table_listener():
    """Listener must be scoped to #names-table, not document body — keeps
    sync() from running on every unrelated swap on the page."""
    assert "getElementById('names-table')" in ADD_NAME_GUARD_JS
    # No body-level htmx:afterSwap listener — only the names-table one.
    assert "document.body.addEventListener('htmx:afterSwap'" not in ADD_NAME_GUARD_JS


def test_add_name_guard_script_handles_new_row_close_event():
    """The custom event from the new-name Cancel re-enables the button."""
    assert "powerMap:newNameRowClosed" in ADD_NAME_GUARD_JS
    assert "name-row-new" in ADD_NAME_GUARD_JS


def test_new_name_form_cancel_dispatches_close_event():
    """The inline-Cancel for new-name forms must dispatch the custom
    event so the + Add name button can re-enable itself."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=None, parts=None, person_id="pid_x")
    # Cancel for the new-name branch removes the row and signals the page.
    assert "powerMap:newNameRowClosed" in out
    # The existing-row Cancel uses hx-get, not onclick; not affected.


def test_existing_row_cancel_unchanged():
    """Existing-row Cancel still issues an HTMX read-row swap, not the
    inline remove + dispatch."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    n = {"id": "nid_x", "name": "X", "name_type": "legal",
         "is_canonical": True, "visibility": "public",
         "locale": None, "script": None, "sort_as": None,
         "reading_of_id": None, "reading_of_name": None}
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=n, parts=None, person_id="pid_x")
    # Existing-row Cancel is HTMX-driven: hx-get to read-row, hx-target the row.
    assert "/names/nid_x/read-row/" in out
    # And its branch should not carry the new-row dispatch.
    cancel_segment = out.split("Cancel</button>")[0].split("Save</button>")[1]
    assert "powerMap:newNameRowClosed" not in cancel_segment


def test_cardstack_inputs_wrapped_in_form_group():
    """Each cardstack card's <input> sits inside a `.form-group` so it
    inherits the baseline input styling (font-size, padding, min-height)
    instead of falling back to the browser default."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    parts = {
        "given_names": ["Ada"],
        "family_names": ["Lovelace"],
        "additional_names": ["Augusta"],
        "honorific_prefix": None,
        "honorific_suffix": None,
        "primary_identifier": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=parts, person_id="pid_x")
    # Each card is a div with data-cardstack-card; inside, the <input>
    # should be inside a .form-group wrapper.
    for field in ("given_names", "family_names", "additional_names"):
        card_segments = out.split(f'data-cardstack-card="{field}"')
        # First segment is before the first card; subsequent segments
        # start inside the cards.
        for seg in card_segments[1:]:
            # Bound the segment to the next card or end-of-stack.
            seg_bounded = seg.split('data-cardstack-card="')[0]
            seg_bounded = seg_bounded.split('data-cardstack-add=')[0]
            assert "form-group" in seg_bounded, (
                f"cardstack input for {field} not wrapped in .form-group "
                f"— styling will fall back to browser default"
            )
