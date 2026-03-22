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


def test_filter_card_search_has_min_height_44():
    """Main filter search input (.filter-card__search) must meet 44 px touch target."""
    match = re.search(r"\.filter-card__search\s*\{[^}]*min-height:\s*44px", CSS)
    assert match, "Expected min-height: 44px on .filter-card__search rule"


def test_hamburger_button_has_min_height_and_width_44():
    """Mobile hamburger toggle must meet 44×44 px touch target — shown only on mobile."""
    match = re.search(r"\.admin-topbar__menu-toggle\s*\{[^}]*min-height:\s*44px", CSS)
    assert match, "Expected min-height: 44px on .admin-topbar__menu-toggle"
    match = re.search(r"\.admin-topbar__menu-toggle\s*\{[^}]*min-width:\s*44px", CSS)
    assert match, "Expected min-width: 44px on .admin-topbar__menu-toggle"


def test_sidebar_links_have_min_height_44():
    """Sidebar nav links must meet 44 px touch target height (tapped after hamburger opens)."""
    match = re.search(r"\.admin-sidebar__link\s*\{[^}]*min-height:\s*44px", CSS)
    assert match, "Expected min-height: 44px on .admin-sidebar__link"


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


# ── Task 3: detail-grid ──────────────────────────────────────────────────────

def test_detail_grid_uses_css_grid():
    """detail-grid must use CSS grid — currently has no CSS at all."""
    match = re.search(r"\.detail-grid\s*\{[^}]*display:\s*grid", CSS)
    assert match, "Expected display:grid on .detail-grid"


def test_detail_grid_two_column_on_desktop():
    """On desktop, detail-grid must be 2-column (label + value)."""
    match = re.search(r"\.detail-grid\s*\{[^}]*grid-template-columns:", CSS)
    assert match, "Expected grid-template-columns on .detail-grid"


def test_detail_grid_stacks_on_mobile():
    """Below 640px, detail-grid must collapse to single-column."""
    # Find the @media (max-width: 640px) block and verify detail-grid is inside it
    mobile_block_match = re.search(
        r"@media\s*\(max-width:\s*640px\)(.*?)(?=@media|\Z)", CSS, re.DOTALL
    )
    assert mobile_block_match, "Expected @media (max-width: 640px) block"
    mobile_block = mobile_block_match.group(1)
    assert ".detail-grid" in mobile_block, (
        "Expected .detail-grid override inside @media (max-width: 640px)"
    )


def test_entity_section_has_margin_top():
    """entity-section must have spacing to separate related-entity panels."""
    match = re.search(r"\.entity-section\s*\{[^}]*margin-top:", CSS)
    assert match, "Expected margin-top on .entity-section"


# ── Task 4: Mobile audit ─────────────────────────────────────────────────────

def test_admin_main_has_reduced_padding_on_mobile():
    """admin-main padding should be reduced inside the 768px media block."""
    mobile_block_match = re.search(
        r"@media\s*\(max-width:\s*768px\)(.*?)(?=@media|\Z)", CSS, re.DOTALL
    )
    assert mobile_block_match, "Expected @media (max-width: 768px) block"
    mobile_block = mobile_block_match.group(1)
    assert ".admin-main" in mobile_block, (
        "Expected .admin-main padding rule inside @media (max-width: 768px)"
    )


def test_filter_card_controls_stack_on_mobile():
    """filter-card controls must stack vertically on narrow screens."""
    mobile_block_match = re.search(
        r"@media\s*\(max-width:\s*640px\)(.*?)(?=@media|\Z)", CSS, re.DOTALL
    )
    assert mobile_block_match, "Expected @media (max-width: 640px) block"
    mobile_block = mobile_block_match.group(1)
    assert ".filter-card__controls" in mobile_block, (
        "Expected .filter-card__controls rule inside @media (max-width: 640px)"
    )


def test_filter_card_field_min_width_unset_on_mobile():
    """filter-card selects must not keep min-width: 200px on narrow screens."""
    mobile_block_match = re.search(
        r"@media\s*\(max-width:\s*640px\)(.*?)(?=@media|\Z)", CSS, re.DOTALL
    )
    assert mobile_block_match
    mobile_block = mobile_block_match.group(1)
    # Look for a rule that resets min-width to 0 or removes it for filter-card fields
    assert "filter-card__field" in mobile_block and "min-width" in mobile_block, (
        "Expected filter-card__field min-width override inside @media (max-width: 640px)"
    )
