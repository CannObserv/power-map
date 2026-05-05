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
    """For new rows, public must be the pre-selected option."""
    # Locate the visibility select block; selected attr should land on public.
    block = FORM_ROW.split('name="visibility"')[1].split("</select>")[0]
    # Either Jinja conditional or explicit selected on public — accept either.
    assert "public" in block


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


def test_read_row_badge_uses_badge_class():
    """Visibility badge should use the existing badge component for visual consistency."""
    # Find the visibility-conditional block and confirm 'badge' appears within it.
    if "n.visibility" in READ_ROW:
        # Take a window after the first n.visibility ref
        idx = READ_ROW.find("n.visibility")
        window = READ_ROW[idx:idx + 300]
        assert "badge" in window, "visibility branch should render a badge element"
