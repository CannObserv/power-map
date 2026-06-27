"""Assert admin.css uses co-purple brand tokens and correct focus glow."""

# Relative paths resolve from repo/worktree root — pytest must be invoked
# from the worktree root (e.g. `cd .worktrees/19-style-guide && uv run pytest`).
import re
from pathlib import Path

CSS = Path("src/static/admin/admin.css").read_text()

_TEMPLATE_DIR = Path("src/templates")
# Unicode ranges covering common emoji blocks
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff"  # Misc symbols and pictographs
    "\U00002600-\U000027bf"  # Misc symbols
    "\U0001f900-\U0001f9ff]"  # Supplemental symbols
)


def test_brand_token_is_co_purple():
    assert "--color-brand: #6d4488" in CSS


def test_brand_hover_token_is_co_purple_700():
    assert "--color-brand-hover: #5a3870" in CSS


def test_border_focus_token_is_co_purple():
    assert "--color-border-focus: #6d4488" in CSS


def test_brand_subtle_token_exists():
    assert "--color-brand-subtle: #f5f0f8" in CSS


def test_brand_subtle_border_token_exists():
    assert "--color-brand-subtle-border: #ebe1f1" in CSS


def test_co_green_token_exists():
    assert re.search(r"--color-green:\s+#8cbe69", CSS)


def test_focus_glow_token_exists():
    assert "--color-brand-glow:" in CSS


def test_no_hardcoded_blue_shadow():
    assert "rgba(37,99,235" not in CSS


def test_dark_mode_class_override_exists():
    assert "html.dark" in CSS


def test_light_mode_class_override_exists():
    assert "html.light" in CSS


def test_dark_class_covers_badge_colors():
    assert "html.dark .badge--active" in CSS
    assert "html.dark .badge--inactive" in CSS
    assert "html.dark .badge--archived" in CSS
    assert "html.dark .alert--notice" in CSS
    assert "html.dark .flash--success" in CSS


def test_light_class_covers_badge_colors():
    assert "html.light .badge--active" in CSS
    assert "html.light .badge--inactive" in CSS
    assert "html.light .badge--archived" in CSS
    assert "html.light .alert--notice" in CSS
    assert "html.light .flash--success" in CSS


def _badge_blocks() -> tuple[str, str]:
    """(base block, @media-dark fallback block) for badge rules.

    Anchors on the base `.badge {` rule, then the *next* `@media
    (prefers-color-scheme: dark)` — the badge fallback, not the earlier `:root`
    token @media block. Bounds the @media slice by the following `@media` so it
    stays correct as rules are inserted."""
    base_start = CSS.index(".badge {")
    media_start = CSS.index("@media (prefers-color-scheme: dark)", base_start)
    next_media = CSS.find("@media", media_start + 10)
    if next_media == -1:  # badge @media is the last one in the file
        next_media = len(CSS)
    return CSS[base_start:media_start], CSS[media_start:next_media]


def test_media_query_dark_covers_badges():
    """No-JS @media fallback must also cover badge colors."""
    assert "prefers-color-scheme: dark" in CSS
    _, media_block = _badge_blocks()
    assert ".badge--active" in media_block
    assert ".badge--inactive" in media_block
    assert ".badge--archived" in media_block


def test_neutral_badge_has_light_dark_parity():
    """#248: badge--neutral (non-US country badge) needs the same four-block
    coverage as every other badge — base, @media dark, html.dark, html.light —
    else it falls back to bare `.badge` (no bg/fg) and renders inconsistently."""
    assert "html.dark .badge--neutral" in CSS
    assert "html.light .badge--neutral" in CSS
    base_block, media_block = _badge_blocks()
    assert ".badge--neutral" in base_block  # base rule
    assert ".badge--neutral" in media_block  # @media dark fallback


def test_neutral_inactive_badges_meet_text_contrast():
    """#248 CR: neutral/inactive badge text must avoid the low-contrast
    --color-inactive token. Light bg (#f1f5f9) pairs with a darker AA hex
    (#556070, ~5.8:1); dark bg (#1e293b) pairs with --color-text-muted (~5.7:1)."""
    badge_lines = [
        ln for ln in CSS.splitlines() if ".badge--inactive" in ln or ".badge--neutral" in ln
    ]
    assert badge_lines, "no badge--inactive/neutral rules found"
    assert not any("--color-inactive" in ln for ln in badge_lines)
    for ln in badge_lines:
        if "#f1f5f9" in ln:  # light bg
            assert "#556070" in ln, ln
        elif "#1e293b" in ln:  # dark bg
            assert "--color-text-muted" in ln, ln


def _template_badge_variants() -> set[str]:
    """Every `badge--X` modifier class referenced in admin templates."""
    variants: set[str] = set()
    for tmpl in _TEMPLATE_DIR.rglob("*.html"):
        variants.update(re.findall(r"badge--([a-z-]+)", tmpl.read_text()))
    return variants


def test_every_template_badge_variant_is_defined_in_css():
    """Every `badge--X` used in a template must have a CSS rule. A referenced-but-
    undefined variant falls back to bare `.badge` (no bg/fg) — the #248 bug class."""
    undefined = sorted(
        v for v in _template_badge_variants() if not re.search(rf"\.badge--{v}\b", CSS)
    )
    assert not undefined, f"badge--X used in templates but undefined in admin.css: {undefined}"


_JS_PATH = Path("src/static/admin/dark-mode.js")
JS = _JS_PATH.read_text() if _JS_PATH.exists() else ""


def test_dark_mode_js_exists():
    assert _JS_PATH.exists()


def test_dark_mode_js_uses_pm_color_scheme_key():
    assert "pm-color-scheme" in JS


def test_dark_mode_js_toggles_dark_class():
    assert "classList" in JS
    assert "'dark'" in JS or '"dark"' in JS


def test_btn_disabled_has_opacity():
    assert ".btn:disabled" in CSS
    assert "opacity: 0.4" in CSS


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
    msg = 'Bare emojis found (wrap in <span aria-hidden="true">):\n' + "\n".join(violations)
    assert not violations, msg
