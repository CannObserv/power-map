"""Static assertions for person-name partial templates (Phase 2a Task 2)."""
from pathlib import Path

FORM_ROW = Path("src/templates/admin/people/partials/_name_form_row.html").read_text()
READ_ROW = Path("src/templates/admin/people/partials/_name_row.html").read_text()
DETAIL = Path("src/templates/admin/people/detail.html").read_text()


# ---------------------------------------------------------------------------
# Form row — expanded name_type options
# ---------------------------------------------------------------------------

ALL_NAME_TYPES = (
    "legal", "preferred", "alias", "former", "initials",
    "maiden", "religious", "stage", "deadname",
    "reading", "romanization", "mrz",
)


def test_form_row_offers_all_twelve_name_types():
    """Form must expose every name_type from CONVENTIONS.md, not just the legacy 5."""
    # Templates use a Jinja for-loop over a tuple literal; match the string form.
    for t in ALL_NAME_TYPES:
        assert f"'{t}'" in FORM_ROW, f"name_type option {t!r} missing from form row"


def test_form_row_name_type_select_is_named_correctly():
    assert 'name="name_type"' in FORM_ROW


# ---------------------------------------------------------------------------
# Form row — visibility select
# ---------------------------------------------------------------------------

VISIBILITY_VALUES = ("public", "legal_only", "hidden")


def test_form_row_has_visibility_select():
    assert 'name="visibility"' in FORM_ROW


def test_form_row_offers_all_three_visibility_values():
    for v in VISIBILITY_VALUES:
        assert f"'{v}'" in FORM_ROW, f"visibility option {v!r} missing"


def test_form_row_visibility_select_has_label():
    """A11y: the visibility control must be labelled."""
    # Either a <label> element, an aria-label, or aria-labelledby reference.
    has_label = (
        'aria-label="Visibility"' in FORM_ROW
        or "<label" in FORM_ROW.split('name="visibility"')[0][-200:]
    )
    assert has_label, "visibility select needs a label or aria-label"


def test_form_row_visibility_defaults_to_public_when_no_existing_row():
    """For new rows (n is None), the public <option> must end up selected.

    The template uses `(not n and v == 'public')` in the option's selected
    expression — verify both the structural pattern and the runtime result.
    """
    # Structural check: the Jinja guard must reference (not n) and 'public'.
    assert "not n and v == 'public'" in FORM_ROW or "v == 'public'" in FORM_ROW

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
    assert 'name="locale"' in FORM_ROW
    assert "/admin/people/_locale_search" in FORM_ROW
    assert 'role="combobox"' in FORM_ROW
    assert 'aria-controls="locale-search-results"' in FORM_ROW
    assert 'aria-haspopup="listbox"' in FORM_ROW


def test_form_row_locale_results_listbox_present():
    assert 'id="locale-search-results"' in FORM_ROW
    assert 'role="listbox"' in FORM_ROW


def test_form_row_has_script_typeahead_combobox():
    assert 'name="script"' in FORM_ROW
    assert "/admin/people/_script_search" in FORM_ROW
    assert 'aria-controls="script-search-results"' in FORM_ROW


def test_form_row_script_results_listbox_present():
    assert 'id="script-search-results"' in FORM_ROW


def test_form_row_has_sort_as_plain_input():
    """sort_as is a plain text input — not a combobox."""
    assert 'name="sort_as"' in FORM_ROW


def test_form_row_calls_init_typeahead_for_locale_and_script():
    """Each combobox must be wired via window.initTypeaheadCombobox(...)."""
    assert FORM_ROW.count("initTypeaheadCombobox") >= 2


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
    assert 'name="reading_of_id"' in FORM_ROW
    assert "_reading_target_search" in FORM_ROW
    assert 'aria-controls="reading-of-results"' in FORM_ROW
    assert 'aria-haspopup="listbox"' in FORM_ROW


def test_form_row_reading_of_results_listbox_present():
    assert 'id="reading-of-results"' in FORM_ROW


def test_form_row_calls_init_typeahead_for_reading_of():
    """Three combobox factories now: locale + script + reading_of."""
    assert FORM_ROW.count("initTypeaheadCombobox") >= 3


def test_form_row_reading_of_block_is_conditional():
    """Block must be wrapped so JS can show/hide based on name_type."""
    # Either a wrapping element with id, or a class hook that the JS toggles.
    assert 'id="reading-of-block"' in FORM_ROW or "data-reading-of-block" in FORM_ROW


def test_form_row_has_reading_type_toggle_script():
    """JS must show the block when name_type ∈ {reading, romanization, mrz}."""
    # Token-level check; no need to parse the JS.
    assert "reading" in FORM_ROW and "romanization" in FORM_ROW and "mrz" in FORM_ROW


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
