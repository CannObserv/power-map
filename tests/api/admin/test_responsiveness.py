"""CSS content assertions for responsiveness fixes (issue #22)."""
import re
from pathlib import Path

CSS = Path("src/static/admin/admin.css").read_text()


# ── Task 1: Touch targets ────────────────────────────────────────────────────

def test_btn_has_min_height_44():
    """All .btn elements must meet 44 px touch target height."""
    match = re.search(r"\.btn\s*\{[^}]*min-height:\s*44px", CSS)
    assert match, "Expected min-height: 44px on .btn rule"


def test_form_group_inputs_have_min_height_44():
    """form-group inputs/selects/textareas must meet 44 px touch target height."""
    match = re.search(
        r"\.form-group\s+input,\s*\.form-group\s+select,\s*\.form-group\s+textarea\s*\{[^}]*min-height:\s*44px",
        CSS,
    )
    assert match, "Expected min-height: 44px on .form-group input/select/textarea rule"


def test_filter_card_selects_have_min_height_44():
    """Filter-card selects must meet 44 px touch target height."""
    match = re.search(
        r"\.filter-card__field\s+select.*?min-height:\s*44px",
        CSS,
        re.DOTALL,
    )
    assert match, "Expected min-height: 44px on .filter-card__field select rule"


def test_checkbox_label_touch_target_css_exists():
    """Checkbox/radio labels need min-height for touch targets."""
    assert "input[type=checkbox]" in CSS or "input[type=radio]" in CSS
    match = re.search(r"label:has\(input\[type=checkbox\]\)[^}]*min-height:\s*44px", CSS, re.DOTALL)
    assert match, "Expected min-height: 44px on label:has(input[type=checkbox]) rule"


# ── Task 2: Sticky thead ─────────────────────────────────────────────────────

def test_data_table_thead_th_is_sticky():
    """thead th must be position:sticky so headers stay visible on scroll."""
    match = re.search(r"\.data-table\s+thead\s+th\s*\{[^}]*position:\s*sticky", CSS)
    assert match, "Expected position:sticky on .data-table thead th"


def test_data_table_thead_th_has_top_zero():
    match = re.search(r"\.data-table\s+thead\s+th\s*\{[^}]*top:\s*0", CSS)
    assert match, "Expected top:0 on .data-table thead th"


def test_data_table_thead_th_has_background():
    """Sticky th needs explicit background so rows don't show through."""
    match = re.search(r"\.data-table\s+thead\s+th\s*\{[^}]*background:", CSS)
    assert match, "Expected background property on .data-table thead th"


def test_data_table_thead_th_has_shadow():
    """Shadow below sticky header separates it from scrolled rows."""
    match = re.search(r"\.data-table\s+thead\s+th\s*\{[^}]*box-shadow:", CSS)
    assert match, "Expected box-shadow on .data-table thead th"
