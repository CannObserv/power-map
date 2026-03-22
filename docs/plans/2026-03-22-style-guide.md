# Style Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `docs/STYLE.md` alongside co-purple branding, class-based dark mode toggle, and accessibility gap fixes across the admin dashboard.

**Architecture:** CSS custom properties drive all theming; `html.dark` / `html.light` class overrides beat the `prefers-color-scheme` media query (kept as no-JS fallback). A tiny synchronous FOUC-prevention script in `<head>` reads `localStorage` and sets the class before first paint. A separate `dark-mode.js` handles the toggle button. All new CSS uses logical properties for i18n groundwork.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, vanilla CSS (custom properties), vanilla JS, pytest, uv

**Worktree:** `.worktrees/19-style-guide` on branch `feature/19-style-guide`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/static/admin/admin.css` | Brand tokens, dark-mode class overrides, toggle button styles |
| Create | `src/static/admin/dark-mode.js` | Toggle click handler, localStorage persistence, aria-label sync |
| Modify | `src/templates/admin/base.html` | FOUC inline script, toggle button, emoji `aria-hidden`, `dark-mode.js` ref |
| Create | `docs/STYLE.md` | Authoritative style reference |
| Modify | `tests/api/admin/test_base_template.py` | New assertions: toggle button, FOUC script, emoji wrapping |
| Create | `tests/api/admin/test_css.py` | CSS token value assertions (reads file; no DB needed) |

---

## Task 1: CSS color tokens — co-purple brand accent

**Files:**
- Modify: `src/static/admin/admin.css:1-36` (`:root` and `@media prefers-color-scheme: dark` blocks)
- Create: `tests/api/admin/test_css.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/api/admin/test_css.py`:

```python
"""Assert admin.css uses co-purple brand tokens and correct focus glow."""

# Relative paths resolve from repo/worktree root — pytest must be invoked
# from the worktree root (e.g. `cd .worktrees/19-style-guide && uv run pytest`).
from pathlib import Path

CSS = Path("src/static/admin/admin.css").read_text()


def test_brand_token_is_co_purple():
    assert "--color-brand: #6d4488" in CSS


def test_brand_hover_token_is_co_purple_700():
    assert "--color-brand-hover: #5a3870" in CSS


def test_border_focus_token_is_co_purple():
    assert "--color-border-focus: #6d4488" in CSS


def test_brand_subtle_token_exists():
    assert "--color-brand-subtle:" in CSS


def test_brand_subtle_border_token_exists():
    assert "--color-brand-subtle-border:" in CSS


def test_co_green_reserved_token_exists():
    assert "--color-green: #8cbe69" in CSS


def test_focus_glow_token_exists():
    assert "--color-brand-glow:" in CSS


def test_no_hardcoded_blue_shadow():
    assert "rgba(37,99,235" not in CSS


def test_dark_mode_class_override_exists():
    assert "html.dark" in CSS


def test_light_mode_class_override_exists():
    assert "html.light" in CSS
```

- [ ] **Step 1.2: Run tests — confirm all fail**

```bash
cd .worktrees/19-style-guide
uv run pytest tests/api/admin/test_css.py -v
```

Expected: all 10 FAIL.

- [ ] **Step 1.3: Update `:root` token block in `admin.css`**

Replace the `:root { ... }` block (lines 3–25) with:

```css
:root {
  --color-brand:               #6d4488;
  --color-brand-hover:         #5a3870;
  --color-brand-subtle:        #f5f0f8;
  --color-brand-subtle-border: #ebe1f1;
  --color-green:               #8cbe69;
  --color-brand-glow:          rgba(109,68,136,0.18);
  --color-surface-0:     #f8fafc;
  --color-surface-1:     #ffffff;
  --color-surface-2:     #1e293b;
  --color-text:          #0f172a;
  --color-text-muted:    #64748b;
  --color-text-inverse:  #f1f5f9;
  --color-border:        #e2e8f0;
  --color-border-focus:  #6d4488;
  --color-success:       #16a34a;
  --color-warning:       #d97706;
  --color-danger:        #dc2626;
  --color-inactive:      #94a3b8;
  --space-1: 0.25rem; --space-2: 0.5rem; --space-3: 0.75rem; --space-4: 1rem;
  --space-5: 1.5rem;  --space-6: 2rem;   --space-7: 3rem;    --space-8: 4rem;
  --radius-sm: 0.25rem; --radius-md: 0.375rem; --radius-lg: 0.5rem;
  --font-family-base: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-size-sm: 0.8125rem; --font-size-md: 0.9375rem;
  --font-size-lg: 1.125rem;  --font-size-xl: 1.375rem;
  --topbar-h: 3.25rem;
}
```

- [ ] **Step 1.4: Update `@media (prefers-color-scheme: dark)` block in `admin.css`**

Replace existing dark media query `:root` overrides (lines 27–36) with:

```css
@media (prefers-color-scheme: dark) {
  :root {
    --color-brand: #a78bc4; --color-brand-hover: #c4aed8;
    --color-brand-subtle: #2d1f38; --color-brand-subtle-border: #4a3060;
    --color-brand-glow: rgba(167,139,196,0.20);
    --color-surface-0: #0f172a; --color-surface-1: #1e293b; --color-surface-2: #0f172a;
    --color-text: #f1f5f9; --color-text-muted: #94a3b8; --color-text-inverse: #0f172a;
    --color-border: #334155; --color-border-focus: #a78bc4;
    --color-success: #4ade80; --color-warning: #fbbf24;
    --color-danger: #f87171; --color-inactive: #475569;
  }
}
```

- [ ] **Step 1.5: Add `html.dark` and `html.light` class overrides in `admin.css`**

> **Note:** The design doc describes this selector as `.dark :root { … }`, but that never matches — `:root` is `<html>` and cannot be a descendant of itself. The correct selector is `html.dark { … }` (specificity 0,1,1 beats `:root` at 0,0,1). The plan intentionally diverges from the design doc here.

Add immediately after the `@media (prefers-color-scheme: dark)` block, covering custom property tokens AND the hardcoded badge/flash/alert color rules (which live outside the token system):

```css
/* Class-based dark mode — wins over media query (set by dark-mode.js)
   Specificity: html.dark (0,1,1) > :root (0,0,1) in @media query */
html.dark {
  --color-brand: #a78bc4; --color-brand-hover: #c4aed8;
  --color-brand-subtle: #2d1f38; --color-brand-subtle-border: #4a3060;
  --color-brand-glow: rgba(167,139,196,0.20);
  --color-surface-0: #0f172a; --color-surface-1: #1e293b; --color-surface-2: #0f172a;
  --color-text: #f1f5f9; --color-text-muted: #94a3b8; --color-text-inverse: #0f172a;
  --color-border: #334155; --color-border-focus: #a78bc4;
  --color-success: #4ade80; --color-warning: #fbbf24;
  --color-danger: #f87171; --color-inactive: #475569;
}
/* Badge colors — hardcoded (not token-based); must mirror @media dark block */
html.dark .badge--active   { background: #14532d; color: #86efac; }
html.dark .badge--inactive { background: #1e293b; color: var(--color-inactive); }
html.dark .badge--archived { background: #450a0a; color: #fca5a5; }
/* Alert colors */
html.dark .alert--success { background: #14532d; color: #86efac; border-color: #166534; }
html.dark .alert--error   { background: #450a0a; color: #fca5a5; border-color: #991b1b; }
html.dark .alert--warning { background: #422006; color: #fde68a; border-color: #713f12; }
html.dark .alert--notice  { background: #1e3a5f; color: #93c5fd; border-color: #1d4ed8; }
/* Flash colors */
html.dark .flash--success { background: #14532d; color: #86efac; border-color: #166534; }
html.dark .flash--info    { background: #1e3a5f; color: #93c5fd; border-color: #1d4ed8; }
html.dark .flash--warning { background: #422006; color: #fde68a; border-color: #713f12; }
html.dark .flash--error   { background: #450a0a; color: #fca5a5; border-color: #991b1b; }

/* Class-based light mode — overrides prefers-color-scheme: dark for explicit choice */
html.light {
  --color-brand: #6d4488; --color-brand-hover: #5a3870;
  --color-brand-subtle: #f5f0f8; --color-brand-subtle-border: #ebe1f1;
  --color-brand-glow: rgba(109,68,136,0.18);
  --color-surface-0: #f8fafc; --color-surface-1: #ffffff; --color-surface-2: #1e293b;
  --color-text: #0f172a; --color-text-muted: #64748b; --color-text-inverse: #f1f5f9;
  --color-border: #e2e8f0; --color-border-focus: #6d4488;
  --color-success: #16a34a; --color-warning: #d97706;
  --color-danger: #dc2626; --color-inactive: #94a3b8;
}
/* Badge/alert/flash light mode — restore defaults when system is dark but user chose light */
html.light .badge--active, html.light .badge--inactive, html.light .badge--archived,
html.light .alert--success, html.light .alert--error, html.light .alert--warning, html.light .alert--notice,
html.light .flash--success, html.light .flash--info, html.light .flash--warning, html.light .flash--error {
  /* Revert to stylesheet defaults (light values are the default; this block
     only needs to exist so it wins over the @media dark query) */
}
html.light .badge--active   { background: #dcfce7; color: #15803d; }
html.light .badge--inactive { background: #f1f5f9; color: var(--color-inactive); }
html.light .badge--archived { background: #fee2e2; color: #991b1b; }
html.light .alert--success  { background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }
html.light .alert--error    { background: #fef2f2; color: #991b1b; border-color: #fecaca; }
html.light .alert--warning  { background: #fef9c3; color: #854d0e; border-color: #fde68a; }
html.light .alert--notice   { background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }
html.light .flash--success  { background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }
html.light .flash--info     { background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }
html.light .flash--warning  { background: #fef9c3; color: #854d0e; border-color: #fde68a; }
html.light .flash--error    { background: #fef2f2; color: #991b1b; border-color: #fecaca; }
```

Also add to `tests/api/admin/test_css.py`:

```python
def test_dark_class_covers_badge_colors():
    assert "html.dark .badge--active" in CSS

def test_dark_class_covers_flash_colors():
    assert "html.dark .flash--success" in CSS

def test_light_class_covers_badge_colors():
    assert "html.light .badge--active" in CSS
```

- [ ] **Step 1.6: Fix hardcoded blue focus shadows in `admin.css`**

Replace the three `rgba(37,99,235,0.15)` occurrences (lines ~140, ~147, ~152) with `var(--color-brand-glow)`:

```css
/* line ~140 */
.form-group input:focus, .form-group select:focus, .form-group textarea:focus { outline: none; border-color: var(--color-border-focus); box-shadow: 0 0 0 3px var(--color-brand-glow); }
/* line ~147 */
.filter-card__search:focus { outline: none; border-color: var(--color-border-focus); box-shadow: 0 0 0 3px var(--color-brand-glow); }
/* line ~152 */
.filter-card__field select:focus, .filter-card__field input[type=search]:focus { outline: none; border-color: var(--color-border-focus); box-shadow: 0 0 0 3px var(--color-brand-glow); }
```

- [ ] **Step 1.7: Run tests — confirm all pass**

```bash
uv run pytest tests/api/admin/test_css.py -v
```

Expected: 10 PASS.

- [ ] **Step 1.8: Commit**

```bash
git add src/static/admin/admin.css tests/api/admin/test_css.py
git commit -m "#19 feat: co-purple brand tokens, dark/light class overrides, brand-glow shadow"
```

---

## Task 2: Dark mode JS — `dark-mode.js`

**Files:**
- Create: `src/static/admin/dark-mode.js`

- [ ] **Step 2.1: Write the failing test**

Add to `tests/api/admin/test_css.py` (do NOT re-import `Path` — it is already imported at the top):

```python
_JS_PATH = Path("src/static/admin/dark-mode.js")
JS = _JS_PATH.read_text() if _JS_PATH.exists() else ""


def test_dark_mode_js_exists():
    assert _JS_PATH.exists()


def test_dark_mode_js_uses_pm_color_scheme_key():
    assert "pm-color-scheme" in JS


def test_dark_mode_js_toggles_dark_class():
    assert "classList" in JS
    assert "'dark'" in JS or '"dark"' in JS
```

- [ ] **Step 2.2: Run tests — confirm they fail**

```bash
uv run pytest tests/api/admin/test_css.py::test_dark_mode_js_exists tests/api/admin/test_css.py::test_dark_mode_js_uses_pm_color_scheme_key tests/api/admin/test_css.py::test_dark_mode_js_toggles_dark_class -v
```

Expected: 3 FAIL.

- [ ] **Step 2.3: Create `src/static/admin/dark-mode.js`**

```javascript
/* Power-Map Admin — dark mode toggle
 * Reads/writes localStorage key 'pm-color-scheme'.
 * FOUC prevention is handled by an inline <script> in base.html <head>.
 */
(function () {
  var KEY = 'pm-color-scheme';
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;

  function isDark() {
    return document.documentElement.classList.contains('dark');
  }

  function applyTheme(dark) {
    var html = document.documentElement;
    html.classList.toggle('dark', dark);
    html.classList.toggle('light', !dark);
    localStorage.setItem(KEY, dark ? 'dark' : 'light');
    btn.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
    btn.querySelector('[data-theme-icon]').textContent = dark ? '\u2600' : '\u263D';
  }

  btn.addEventListener('click', function () {
    applyTheme(!isDark());
  });

  /* Sync button label/icon with current state on load */
  btn.setAttribute('aria-label', isDark() ? 'Switch to light mode' : 'Switch to dark mode');
  btn.querySelector('[data-theme-icon]').textContent = isDark() ? '\u2600' : '\u263D';
})();
```

> **Icon note:** `\u2600` = ☀ (sun), `\u263D` = ☽ (crescent moon). These are symbols, not emoji — no `aria-hidden` wrapper needed.

- [ ] **Step 2.4: Run tests — confirm they pass**

```bash
uv run pytest tests/api/admin/test_css.py -v
```

Expected: all PASS.

- [ ] **Step 2.5: Commit**

```bash
git add src/static/admin/dark-mode.js tests/api/admin/test_css.py
git commit -m "#19 feat: add dark-mode.js toggle (localStorage persistence, aria-label sync)"
```

---

## Task 3: Toggle button styles in `admin.css`

**Files:**
- Modify: `src/static/admin/admin.css`

No new tests needed — button uses existing `.btn`, `.btn--ghost`, `.btn--sm` classes. Add only the positioning/size rule.

- [ ] **Step 3.1: Add theme toggle styles to `admin.css`**

After `.admin-topbar__menu-toggle` block (around line ~68), add:

```css
/* Dark mode toggle button */
.admin-topbar__theme-toggle { font-size: 1.1rem; line-height: 1; min-width: 2.25rem; }
```

- [ ] **Step 3.2: Run full test suite — confirm no regressions**

```bash
uv run pytest -x -q
```

Expected: same pass count as baseline (106 non-integration tests).

- [ ] **Step 3.3: Commit**

```bash
git add src/static/admin/admin.css
git commit -m "#19 feat: add theme toggle button styles to admin.css"
```

---

## Task 4: base.html — FOUC script, toggle button, `dark-mode.js` ref

**Files:**
- Modify: `src/templates/admin/base.html`
- Modify: `tests/api/admin/test_base_template.py`

- [ ] **Step 4.1: Write the failing tests**

Add to `tests/api/admin/test_base_template.py`:

```python
def test_fouc_prevention_script_in_base_template(client):
    """FOUC script must appear in base.html to prevent flash on load."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert "pm-color-scheme" in response.text


def test_dark_mode_toggle_button_present(client):
    """Theme toggle button must be in the topbar with correct ARIA label."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert "theme-toggle" in response.text
    assert "Switch to dark mode" in response.text


def test_dark_mode_js_loaded_with_defer(client):
    """dark-mode.js must be loaded with defer to avoid blocking render."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert "dark-mode.js" in response.text
    # Check defer appears on the same script tag line
    text = response.text
    idx = text.find("dark-mode.js")
    assert "defer" in text[max(0, idx-100):idx+100]
```

- [ ] **Step 4.2: Run tests — confirm they fail**

```bash
uv run pytest tests/api/admin/test_base_template.py::test_fouc_prevention_script_in_base_template tests/api/admin/test_base_template.py::test_dark_mode_toggle_button_present tests/api/admin/test_base_template.py::test_dark_mode_js_loaded_with_defer -v
```

Expected: 3 FAIL (integration tests — requires `TEST_DATABASE_URL`; skip if no DB, fix HTML regardless).

> **No DB available?** Test against template file content directly:
> ```python
> html = Path("src/templates/admin/base.html").read_text()
> assert "pm-color-scheme" in html
> ```

- [ ] **Step 4.3: Add FOUC inline script in `base.html` `<head>`**

In `base.html`, insert before the `<link rel="stylesheet">` line:

```html
  <script>
    /* FOUC prevention: apply dark/light class before first paint */
    (function(){
      var k='pm-color-scheme', s=localStorage.getItem(k),
          d=window.matchMedia('(prefers-color-scheme: dark)').matches;
      var html=document.documentElement;
      if(s==='dark'||(s===null&&d)){html.classList.add('dark');}
      else if(s==='light'){html.classList.add('light');}
    })();
  </script>
```

- [ ] **Step 4.4: Add toggle button in topbar in `base.html`**

Inside `.admin-topbar__user` div, add the button as the first child:

```html
      <div class="admin-topbar__user">
        <button class="btn btn--ghost btn--sm admin-topbar__theme-toggle"
                id="theme-toggle"
                aria-label="Switch to dark mode"
                type="button">
          <span data-theme-icon aria-hidden="true">☽</span>
        </button>
        <span>{{ user.email }}</span>
        <form method="POST" action="/__exe.dev/logout" style="margin:0">
          <button type="submit" class="btn btn--ghost btn--sm">Log out</button>
        </form>
      </div>
```

- [ ] **Step 4.5: Add `dark-mode.js` script ref in `base.html`**

Replace the closing `</body>` area — after the existing inline nav script, add:

```html
  <script src="/static/admin/dark-mode.js" defer></script>
</body>
```

- [ ] **Step 4.6: Run tests — confirm they pass**

```bash
uv run pytest tests/api/admin/test_base_template.py -v
```

If integration tests need DB, verify template file content:

```bash
grep -c "pm-color-scheme" src/templates/admin/base.html
grep -c "theme-toggle" src/templates/admin/base.html
grep -c "dark-mode.js" src/templates/admin/base.html
```

Expected: each returns `1`.

- [ ] **Step 4.7: Commit**

```bash
git add src/templates/admin/base.html tests/api/admin/test_base_template.py
git commit -m "#19 feat: FOUC prevention, dark mode toggle button, dark-mode.js in base.html"
```

---

## Task 5: Accessibility — wrap footer emojis in `aria-hidden`

**Files:**
- Modify: `src/templates/admin/base.html`
- Modify: `tests/api/admin/test_base_template.py`

- [ ] **Step 5.1: Update `test_footer_emoji_present` to assert `aria-hidden` wrapping**

The existing test only checks the emoji string is present. Update it:

```python
def test_footer_emoji_present(client):
    """Footer must include the 🌱🏛️🔍 emoji wrapped in aria-hidden span."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "🌱🏛️🔍" in response.text
    assert 'aria-hidden="true">🌱🏛️🔍' in response.text
```

- [ ] **Step 5.2: Run test — confirm it fails**

```bash
uv run pytest tests/api/admin/test_base_template.py::test_footer_emoji_present -v
```

Expected: FAIL (`aria-hidden` not yet present).

- [ ] **Step 5.3: Fix bare emojis in `base.html` footer**

Replace line 47 (`🌱🏛️🔍`) with:

```html
        <span aria-hidden="true">🌱🏛️🔍</span>
```

- [ ] **Step 5.4: Run test — confirm it passes**

```bash
uv run pytest tests/api/admin/test_base_template.py::test_footer_emoji_present -v
```

Expected: PASS.

- [ ] **Step 5.5: Run full test suite — confirm no regressions**

```bash
uv run pytest -x -q
```

- [ ] **Step 5.6: Commit**

```bash
git add src/templates/admin/base.html tests/api/admin/test_base_template.py
git commit -m "#19 fix: wrap footer emojis in aria-hidden span (accessibility)"
```

---

## Task 6: Emoji audit across all templates

**Files:**
- Read: all `src/templates/admin/**/*.html`
- Modify: `tests/api/admin/test_css.py`

- [ ] **Step 6.1: Write a test asserting no bare emojis in templates**

Add to `tests/api/admin/test_css.py`:

```python
import re

_TEMPLATE_DIR = Path("src/templates")
# Unicode ranges covering common emoji blocks
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF"   # Misc symbols and pictographs
    "\U00002600-\U000027BF"    # Misc symbols
    "\U0001F900-\U0001F9FF]"   # Supplemental symbols
)


def test_no_bare_emojis_in_templates():
    """All emojis must be wrapped in <span aria-hidden="true">."""
    violations = []
    for tmpl in _TEMPLATE_DIR.rglob("*.html"):
        text = tmpl.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip lines that already have aria-hidden wrapping
            if 'aria-hidden="true"' in line:
                continue
            if _EMOJI_RE.search(line):
                violations.append(f"{tmpl}:{lineno}: {line.strip()[:80]}")
    assert not violations, "Bare emojis found (wrap in <span aria-hidden=\"true\">):\n" + "\n".join(violations)
```

- [ ] **Step 6.2: Run test — confirm it fails (base.html footer emoji not yet wrapped)**

```bash
uv run pytest tests/api/admin/test_css.py::test_no_bare_emojis_in_templates -v
```

Expected: FAIL — reports `src/templates/admin/base.html:47`.

> This test will pass after Task 5 (emoji wrapping) is complete. Run it again there to confirm no other violations exist.

- [ ] **Step 6.3: After Task 5, re-run to confirm all templates clean**

```bash
uv run pytest tests/api/admin/test_css.py::test_no_bare_emojis_in_templates -v
```

Expected: PASS.

- [ ] **Step 6.4: Commit**

```bash
git add tests/api/admin/test_css.py
git commit -m "#19 test: assert no bare emojis in any admin template"
```

---

## Task 7: `aria-live` verification on HTMX swap targets

**Files:**
- Read: all `src/templates/admin/**/*.html`
- Modify: templates missing `aria-live` on HTMX swap targets

- [ ] **Step 7.1: Audit HTMX swap targets for `aria-live`**

Run:

```bash
grep -rn "hx-target\|hx-swap" src/templates/admin/ | grep -v "oob\|flash-region" | head -40
```

For each element that is an HTMX swap target (i.e., the element referenced by `hx-target`), verify it has `aria-live="polite" aria-atomic="false"`. The `#flash-region` in `base.html` already has this ✓.

- [ ] **Step 7.2: Add `aria-live` to any list view containers missing it**

Common pattern: search result containers, list wrappers that HTMX replaces:

```html
<div id="results-container" aria-live="polite" aria-atomic="false">
```

Add `aria-live="polite" aria-atomic="false"` to any HTMX swap target `<div>` that displays dynamically updated content (search results, filtered lists). If no targets are missing it, document this finding in the commit message.

- [ ] **Step 7.3: Commit**

```bash
git add src/templates/
git commit -m "#19 fix: add aria-live to HTMX swap targets in list views (or: verified all covered)"
```

---

## Task 8: `docs/STYLE.md` — comprehensive style guide

**Files:**
- Create: `docs/STYLE.md`

No automated tests. Content must cover all sections from the design doc.

- [ ] **Step 8.1: Create `docs/STYLE.md`**

Create with these sections (fill in content from existing code — exact token values, class names, patterns):

```markdown
# Power-Map Admin Style Guide

Cannabis Observer brand + visual conventions for all Jinja2 admin templates.
Single authoritative reference for human and AI contributors.

## Brand Assets
## Color Palette
## Dark Mode
## CSS Design Token System
## Layout Conventions
## Responsive Breakpoints
## HTMX Patterns
## Flash / Notification UX
## Pagination Conventions
## Destructive Actions
## Dedup Workflow
## Accessibility (WCAG 2.1 AA)
## Internationalization Groundwork
## Performance Rules
```

Fill each section with:
- **Brand Assets**: SVG paths, sizes, footer emoji sequence, aria-hidden pattern
- **Color Palette**: full token table (name, light value, dark value, purpose); semantic vs. brand distinction
- **Dark Mode**: FOUC script explanation, `html.dark` / `html.light` classes, `pm-color-scheme` localStorage key, toggle button pattern
- **CSS Design Token System**: how to add tokens, naming conventions (`--color-*`, `--space-*`, `--font-size-*`, `--radius-*`), where to define them
- **Layout Conventions**: `admin-layout` grid (3 rows: `auto 1fr auto`), `height: 100dvh`, `admin-main` as scroll container, sidebar as fixed drawer on mobile
- **Responsive Breakpoints**: 768px (mobile nav/sidebar), 640px (action button stacking)
- **HTMX Patterns**: `_is_htmx(request)` guard, OOB flash injection, mutation form pattern (`hx-post` + `hx-target` + `hx-swap="outerHTML"`), loading states (`.htmx-request` CSS)
- **Flash / Notification UX**: macro usage (`message` vs `oob`), levels (success/info/warning/error), `auto_dismiss_ms`, hover-pause, always `markupsafe.escape()` DB values
- **Pagination**: `.pagination` (top bar) + `.pagination--sticky` (footer), page-size select (25/50/100/250)
- **Destructive Actions**: archive-gate pattern (`archived_at IS NOT NULL` before delete), flash confirmation, HTMX in-place update
- **Dedup Workflow**: banner on list → `/duplicates/` review screen → merge or dismiss with OOB flash
- **Accessibility**: WCAG 2.1 AA baseline; emoji aria-hidden rule; focus rings (`:focus-visible`, `--color-border-focus`); ARIA on HTMX targets (`aria-live="polite" aria-atomic="false"`); icon-only button `aria-label`; no `title` attributes; muted text minimum (`--color-text-muted`); skip link pattern
- **i18n Groundwork**: logical properties (list of preferred properties); string externalization rule; date/number formatting pattern (Babel path); `lang`/`dir` attributes; `charset="utf-8"` requirement; NFC normalization note
- **Performance**: no CDN scripts, `defer` on all JS, static asset `Cache-Control`, image explicit `width`/`height`

- [ ] **Step 8.2: Run linter to catch any issues**

```bash
uv run ruff check .
```

Expected: no new errors.

- [ ] **Step 8.3: Commit**

```bash
git add docs/STYLE.md
git commit -m "#19 docs: add comprehensive STYLE.md style guide"
```

---

## Task 9: Final verification

- [ ] **Step 9.1: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: 106+ tests pass, 0 fail.

- [ ] **Step 9.2: Run linter**

```bash
uv run ruff check .
```

Expected: no errors.

- [ ] **Step 9.3: Verify CSS has no remaining hardcoded blue hex**

```bash
grep -n "#2563eb\|#1d4ed8\|rgba(37,99,235" src/static/admin/admin.css
```

Expected: no output.

- [ ] **Step 9.4: Verify dark mode script in base.html head**

```bash
grep -n "pm-color-scheme\|theme-toggle\|dark-mode.js" src/templates/admin/base.html
```

Expected: 3 matches (FOUC script, button, script tag).

- [ ] **Step 9.5: Commit any final fixes, then push**

```bash
git push -u origin feature/19-style-guide
```
