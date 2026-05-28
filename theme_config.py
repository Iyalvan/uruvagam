"""
Shared presentation theme loading for PPTX and HTML renderers.

Themes are deterministic YAML files under themes/. This keeps styling
configurable without making layout depend on an LLM at render time.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_THEME_NAME = "dark_corporate"

DEFAULT_THEME: dict[str, Any] = {
    "name": DEFAULT_THEME_NAME,
    "fonts": {
        "heading": "Aptos Display",
        "body": "Aptos",
        "css": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    },
    "colors": {
        "deck_background": "#0D0D1A",
        "slide_background": "#1A1A2E",
        "surface": "#16213E",
        "elevated": "#0F3D6E",
        "accent": "#53C4FF",
        "accent_2": "#2A7AAA",
        "text": "#FFFFFF",
        "body_text": "#D8DFEB",
        "muted": "#8A9ABB",
        "row_odd": "#0A2A50",
        "row_even": "#16213E",
        "control_border": "#1E2A3A",
        "notes_background": "#0A1220",
        "pip_background": "#0A0A15",
    },
    "ppt": {
        "footer_text": "Generated with uruvagam",
        "logo_width_inches": 1.05,
        "layout_title": "Title Slide White",
        "layout_content": "Title and Content",
        "layout_summary": "Title and Content",
        "layout_blank": "Blank",
    },
    "html": {
        "tagline": "Generated with uruvagam",
        "footer_text": "Generated with uruvagam",
        "slide_shadow": "0 20px 60px rgba(0,0,0,.6)",
    },
}


def load_theme(theme: str | None = None) -> dict[str, Any]:
    """Load a named theme or YAML path, merged over DEFAULT_THEME."""
    theme = theme or DEFAULT_THEME_NAME
    path = _theme_path(theme)
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"theme must be a mapping: {path}")
        data = loaded
    elif theme != DEFAULT_THEME_NAME:
        raise FileNotFoundError(f"theme not found: {theme}")

    merged = _deep_merge(DEFAULT_THEME, data)
    merged.setdefault("name", path.stem if path.exists() else theme)
    return merged


def css_vars(theme: dict[str, Any]) -> str:
    colors = theme.get("colors", {})
    fonts = theme.get("fonts", {})
    html = theme.get("html", {})
    values = {
        "deck-bg": colors["deck_background"],
        "navy": colors["slide_background"],
        "surface": colors["surface"],
        "elevated": colors["elevated"],
        "cyan": colors["accent"],
        "cyan2": colors["accent_2"],
        "white": colors["text"],
        "offwhite": colors["body_text"],
        "muted": colors["muted"],
        "num-bg": colors["row_odd"],
        "row-even": colors["row_even"],
        "control-border": colors["control_border"],
        "notes-bg": colors["notes_background"],
        "pip-bg": colors["pip_background"],
        "slide-shadow": html.get("slide_shadow", "0 20px 60px rgba(0,0,0,.6)"),
        "font": fonts["css"],
    }
    return "\n".join(f"    --{key}: {value};" for key, value in values.items())


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected 6-digit hex color, got {value!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _theme_path(theme: str) -> Path:
    path = Path(theme)
    if path.suffix in {".yaml", ".yml"} or path.parent != Path("."):
        return path
    return Path("themes") / f"{theme}.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
