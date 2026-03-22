"""Assert admin.css uses co-purple brand tokens and correct focus glow."""

# Relative paths resolve from repo/worktree root — pytest must be invoked
# from the worktree root (e.g. `cd .worktrees/19-style-guide && uv run pytest`).
import re
from pathlib import Path

CSS = Path("src/static/admin/admin.css").read_text()


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
    assert "html.dark .badge--archived" in CSS
    assert "html.dark .alert--notice" in CSS
    assert "html.dark .flash--success" in CSS


def test_light_class_covers_badge_colors():
    assert "html.light .badge--active" in CSS
    assert "html.light .badge--archived" in CSS
    assert "html.light .alert--notice" in CSS
    assert "html.light .flash--success" in CSS
