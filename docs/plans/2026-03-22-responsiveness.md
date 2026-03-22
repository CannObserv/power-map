# Responsiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all responsiveness gaps in admin list views and detail/form pages per issue #22.

**Architecture:** Pure CSS changes in `src/static/admin/admin.css` — no template changes required. `table-wrapper` already provides `overflow-x: auto` on all list views. Detail-grid and entity-section classes exist in templates but have no CSS definitions. Tests live in a new `tests/api/admin/test_responsiveness.py` file using CSS content assertions (no DB, fast, not integration-marked).

**Tech Stack:** Hand-rolled CSS design token system, pytest CSS-content assertions.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/static/admin/admin.css` | Modify | All responsive CSS: touch targets, sticky thead, detail-grid, entity-section, mobile tweaks |
| `tests/api/admin/test_responsiveness.py` | Create | CSS content assertions for every new rule — fails before CSS is written |

No template changes needed. `table-wrapper` (`overflow-x: auto`) is already applied to every table in every list and detail view.

---

## Task 1: Touch targets — min-height 44px

Apple HIG and WCAG 2.5.5 require interactive targets ≥ 44×44 CSS px. Currently `.btn` and filter selects have no `min-height`.

**Files:**
- Test: `tests/api/admin/test_responsiveness.py`
- Modify: `src/static/admin/admin.css`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/admin/test_responsiveness.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "min_height or touch" -v
```

Expected: all FAIL — `AssertionError`

- [ ] **Step 3: Add min-height CSS**

In `src/static/admin/admin.css`, find the `.btn` rule (around line 160) and add `min-height: 44px;`:

```css
.btn { display: inline-flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-4); font-size: var(--font-size-sm); font-weight: 500; border-radius: var(--radius-md); border: 1px solid transparent; cursor: pointer; text-decoration: none; transition: background 0.15s, border-color 0.15s; white-space: nowrap; min-height: 44px; }
```

Find `.form-group input, .form-group select, .form-group textarea` (around line 201) and add `min-height: 44px;`:

```css
.form-group input, .form-group select, .form-group textarea { width: 100%; padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); font-family: inherit; font-size: var(--font-size-md); color: var(--color-text); background: var(--color-surface-1); transition: border-color 0.15s; min-height: 44px; }
```

Find `.filter-card__field select, .filter-card__field input[type=search]` (around line 213) and add `min-height: 44px;`:

```css
.filter-card__field select, .filter-card__field input[type=search] { padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); font-family: inherit; font-size: var(--font-size-sm); color: var(--color-text); background: var(--color-surface-1); min-width: 200px; min-height: 44px; }
```

Add a new rule after `.form-group label` for checkbox/radio touch targets:

```css
/* Touch target for checkbox/radio labels */
.form-group label:has(input[type=checkbox]),
.form-group label:has(input[type=radio]) { min-height: 44px; display: flex; align-items: center; gap: var(--space-2); }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "min_height or touch" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/static/admin/admin.css tests/api/admin/test_responsiveness.py
git commit -m "#22 feat: enforce min-height 44px touch targets on interactive elements"
```

---

## Task 2: Sticky thead with shadow

Tables in list views are paginated; users scroll down to see rows, then need to scroll back up to read headers. Sticky `<thead>` fixes this.

**Files:**
- Test: `tests/api/admin/test_responsiveness.py`
- Modify: `src/static/admin/admin.css`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/admin/test_responsiveness.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "thead" -v
```

Expected: all FAIL

- [ ] **Step 3: Add sticky thead CSS**

After the existing `.data-table th { ... }` rule (around line 183), add:

```css
.data-table thead th { position: sticky; top: 0; z-index: 1; background: var(--color-surface-1); box-shadow: 0 1px 0 var(--color-border); }
```

Note: `box-shadow: 0 1px 0 var(--color-border)` renders a 1px separator below each `th` cell. This replaces `border-bottom` visually for sticky cells (borders on sticky elements disappear at sub-pixel boundaries in some browsers).

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "thead" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/static/admin/admin.css tests/api/admin/test_responsiveness.py
git commit -m "#22 feat: sticky thead with shadow on all data-table instances"
```

---

## Task 3: detail-grid and entity-section CSS

`.detail-grid` is used as `<dl class="detail-grid">` in every detail view (`people`, `orgs`, `roles`, `role_assignments`, `imports/batch_detail`). It has zero CSS — renders as browser-default `<dl>` with block `dt`/`dd` stacking. The issue requires `grid-cols-1 sm:grid-cols-2` — i.e., 2-column label/value layout on `≥ 640px`, single-column below.

`.entity-section` wraps each related-entity table section on detail pages. Also has zero CSS.

**Files:**
- Test: `tests/api/admin/test_responsiveness.py`
- Modify: `src/static/admin/admin.css`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/admin/test_responsiveness.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "detail_grid or entity_section" -v
```

Expected: all FAIL

- [ ] **Step 3: Add detail-grid and entity-section CSS**

After the `.entity-card__label` rule (end of entity-card section, around line 194), add:

```css
/* Detail grid — 2-col label/value on ≥640px, single-col on mobile */
.detail-grid { display: grid; grid-template-columns: minmax(140px, max-content) 1fr; gap: var(--space-1) var(--space-4); margin: 0; align-items: baseline; }
.detail-grid dt { font-size: var(--font-size-sm); font-weight: 600; color: var(--color-text-muted); padding-block: var(--space-2); }
.detail-grid dd { margin: 0; padding-block: var(--space-2); border-bottom: 1px solid var(--color-border); }
.detail-grid dd:last-child { border-bottom: none; }

/* Entity section — related-data panels on detail views */
.entity-section { margin-top: var(--space-6); }
.entity-section h2 { margin: 0 0 var(--space-3); font-size: var(--font-size-md); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted); }
```

In the existing `@media (max-width: 640px)` block (around line 257), add:

```css
  .detail-grid { grid-template-columns: 1fr; }
  .detail-grid dt { padding-bottom: 0; border-bottom: none; }
  .detail-grid dd { padding-top: 0; }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "detail_grid or entity_section" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/static/admin/admin.css tests/api/admin/test_responsiveness.py
git commit -m "#22 feat: add detail-grid responsive CSS and entity-section spacing"
```

---

## Task 4: Mobile audit — admin padding and filter-card min-width

Two additional gaps found during audit:

1. `admin-main` has `padding: var(--space-6)` (2rem) on all screen sizes — on narrow screens this wastes horizontal space.
2. `filter-card__field select/input` has `min-width: 200px` — overflows narrow screens. At ≤640px the filter controls should stack vertically and each control should stretch full-width.

**Files:**
- Test: `tests/api/admin/test_responsiveness.py`
- Modify: `src/static/admin/admin.css`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/admin/test_responsiveness.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "mobile or admin_main or filter_card" -v
```

Expected: all FAIL

- [ ] **Step 3: Add mobile CSS**

In the existing `@media (max-width: 768px)` block (around line 137), add inside the block:

```css
  .admin-main { padding: var(--space-4); }
```

In the existing `@media (max-width: 640px)` block (around line 257, near `.dup-actions`), add:

```css
  .filter-card__controls { flex-direction: column; align-items: stretch; }
  .filter-card__field select,
  .filter-card__field input[type=search] { min-width: 0; width: 100%; }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -k "mobile or admin_main or filter_card" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/static/admin/admin.css tests/api/admin/test_responsiveness.py
git commit -m "#22 feat: reduce admin-main padding on mobile, stack filter controls on narrow screens"
```

---

## Task 5: Full suite verification and linting

- [ ] **Step 1: Run all responsiveness tests**

```bash
uv run pytest tests/api/admin/test_responsiveness.py -v
```

Expected: all PASS

- [ ] **Step 2: Run existing CSS tests (regression check)**

```bash
uv run pytest tests/api/admin/test_css.py -v
```

Expected: all PASS — no regressions from CSS changes.

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: all non-integration tests pass. Integration tests skipped unless `TEST_DATABASE_URL` is set.

- [ ] **Step 4: Lint**

```bash
uv run ruff check .
```

Expected: no errors. (Ruff checks Python only.)

- [ ] **Step 5: Final commit if fixups needed**

```bash
git add -p
git commit -m "#22 fix: responsiveness fixups"
```
