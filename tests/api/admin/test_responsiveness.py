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
