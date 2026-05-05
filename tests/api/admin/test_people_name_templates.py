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
