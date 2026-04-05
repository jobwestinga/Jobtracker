"""
Theme engine for JobTracker.

Two independent axes:
  • FX style   — visual effects (Clean, Glow, Glassmorphism, Neon)
  • Colour palette — base hues (Ocean, Emerald, Rose, Amber, Violet, Slate, Arctic, Sunset, Obsidian, Lagoon)

`get_tokens(fx, palette)` returns a flat dict of CSS-ready design tokens.
"""

from typing import Dict


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert #RRGGBB into (r, g, b); fallback to black if invalid."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return (0, 0, 0)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def _rgba(hex_color: str, alpha: int) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {max(0, min(255, alpha))})"

# ── Colour palettes ──────────────────────────────────────────────────────────

PALETTES: Dict[str, Dict[str, str]] = {
    "Ocean": {
        "BG_PRIMARY":    "#0B1120",
        "BG_SECONDARY":  "#131D2E",
        "BG_TERTIARY":   "#1A2640",
        "BORDER_COLOR":  "#1E3050",
        "BORDER_FOCUS":  "#3B82F6",
        "TEXT_PRIMARY":  "#E2E8F0",
        "TEXT_SECONDARY": "#94A3B8",
        "TEXT_DIMMED":   "#475569",
        "ACCENT":        "#3B82F6",
        "ACCENT_GREEN":  "#22C55E",
        "ACCENT_RED":    "#EF4444",
    },
    "Emerald": {
        "BG_PRIMARY":    "#071210",
        "BG_SECONDARY":  "#0F1F1B",
        "BG_TERTIARY":   "#162E28",
        "BORDER_COLOR":  "#1A3D33",
        "BORDER_FOCUS":  "#10B981",
        "TEXT_PRIMARY":  "#E2F0E8",
        "TEXT_SECONDARY": "#8CB8A3",
        "TEXT_DIMMED":   "#456959",
        "ACCENT":        "#10B981",
        "ACCENT_GREEN":  "#34D399",
        "ACCENT_RED":    "#F87171",
    },
    "Rose": {
        "BG_PRIMARY":    "#160B11",
        "BG_SECONDARY":  "#231320",
        "BG_TERTIARY":   "#331A2D",
        "BORDER_COLOR":  "#3D1E38",
        "BORDER_FOCUS":  "#F43F5E",
        "TEXT_PRIMARY":  "#F0E2E8",
        "TEXT_SECONDARY": "#B894A3",
        "TEXT_DIMMED":   "#694559",
        "ACCENT":        "#F43F5E",
        "ACCENT_GREEN":  "#4ADE80",
        "ACCENT_RED":    "#FB7185",
    },
    "Amber": {
        "BG_PRIMARY":    "#130F07",
        "BG_SECONDARY":  "#211A0F",
        "BG_TERTIARY":   "#302514",
        "BORDER_COLOR":  "#3D2E1A",
        "BORDER_FOCUS":  "#F59E0B",
        "TEXT_PRIMARY":  "#F0E8D8",
        "TEXT_SECONDARY": "#B8A080",
        "TEXT_DIMMED":   "#695C45",
        "ACCENT":        "#F59E0B",
        "ACCENT_GREEN":  "#84CC16",
        "ACCENT_RED":    "#EF4444",
    },
    "Violet": {
        "BG_PRIMARY":    "#0E0B18",
        "BG_SECONDARY":  "#181326",
        "BG_TERTIARY":   "#221A38",
        "BORDER_COLOR":  "#2D1E4D",
        "BORDER_FOCUS":  "#8B5CF6",
        "TEXT_PRIMARY":  "#EAE2F0",
        "TEXT_SECONDARY": "#A594B8",
        "TEXT_DIMMED":   "#5E4569",
        "ACCENT":        "#8B5CF6",
        "ACCENT_GREEN":  "#22C55E",
        "ACCENT_RED":    "#F43F5E",
    },
    "Slate": {
        "BG_PRIMARY":    "#111113",
        "BG_SECONDARY":  "#1A1A1E",
        "BG_TERTIARY":   "#252529",
        "BORDER_COLOR":  "#303036",
        "BORDER_FOCUS":  "#A1A1AA",
        "TEXT_PRIMARY":  "#E4E4E7",
        "TEXT_SECONDARY": "#A1A1AA",
        "TEXT_DIMMED":   "#52525B",
        "ACCENT":        "#A1A1AA",
        "ACCENT_GREEN":  "#22C55E",
        "ACCENT_RED":    "#EF4444",
    },
    "Arctic": {
        "BG_PRIMARY":    "#08131A",
        "BG_SECONDARY":  "#10212B",
        "BG_TERTIARY":   "#193240",
        "BORDER_COLOR":  "#254557",
        "BORDER_FOCUS":  "#38BDF8",
        "TEXT_PRIMARY":  "#E5F6FF",
        "TEXT_SECONDARY": "#9CC2D4",
        "TEXT_DIMMED":   "#4B7082",
        "ACCENT":        "#38BDF8",
        "ACCENT_GREEN":  "#22D3EE",
        "ACCENT_RED":    "#F87171",
    },
    "Sunset": {
        "BG_PRIMARY":    "#1A0F0B",
        "BG_SECONDARY":  "#2A1710",
        "BG_TERTIARY":   "#3B2116",
        "BORDER_COLOR":  "#553126",
        "BORDER_FOCUS":  "#F97316",
        "TEXT_PRIMARY":  "#FFECDD",
        "TEXT_SECONDARY": "#D7AA93",
        "TEXT_DIMMED":   "#8C6151",
        "ACCENT":        "#F97316",
        "ACCENT_GREEN":  "#FB923C",
        "ACCENT_RED":    "#EF4444",
    },
    "Obsidian": {
        "BG_PRIMARY":    "#050507",
        "BG_SECONDARY":  "#0D0E12",
        "BG_TERTIARY":   "#161821",
        "BORDER_COLOR":  "#232734",
        "BORDER_FOCUS":  "#7C8599",
        "TEXT_PRIMARY":  "#ECEFF5",
        "TEXT_SECONDARY": "#A5ADBE",
        "TEXT_DIMMED":   "#596173",
        "ACCENT":        "#7C8599",
        "ACCENT_GREEN":  "#22C55E",
        "ACCENT_RED":    "#EF4444",
    },
    "Lagoon": {
        "BG_PRIMARY":    "#071616",
        "BG_SECONDARY":  "#0D2424",
        "BG_TERTIARY":   "#163737",
        "BORDER_COLOR":  "#1E4D4D",
        "BORDER_FOCUS":  "#14B8A6",
        "TEXT_PRIMARY":  "#E5FAF7",
        "TEXT_SECONDARY": "#9CC8C3",
        "TEXT_DIMMED":   "#4F7772",
        "ACCENT":        "#14B8A6",
        "ACCENT_GREEN":  "#2DD4BF",
        "ACCENT_RED":    "#F87171",
    },
}

# ── FX styles ────────────────────────────────────────────────────────────────
# Each FX style defines overrides for visual effects that work on top of any palette.

FX_STYLES: Dict[str, Dict[str, str]] = {
    "Clean": {
        "CARD_BORDER_STYLE": "1px solid {BORDER_COLOR}",
        "CARD_BG":           "{BG_SECONDARY_A220}",
        "CARD_HOVER_BG":     "{BG_TERTIARY_A230}",
        "CARD_HOVER_BORDER": "1px solid {ACCENT}40",
        "TIMER_BG":          "{BG_SECONDARY_A220}",
        "TIMER_BORDER":      "1.5px solid {ACCENT}",
        "IDLE_BORDER":       "1.5px dashed {BORDER_COLOR}",
        "RADIUS":            "14px",
        "SUBJECT_RADIUS":    "16px",
        "TASK_RADIUS":       "14px",
    },
    "Glow": {
        "CARD_BORDER_STYLE": "1px solid {ACCENT}55",
        "CARD_BG":           "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {BG_SECONDARY_A160}, stop:1 {BG_TERTIARY_A160})",
        "CARD_HOVER_BG":     "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {BG_TERTIARY_A180}, stop:1 {BG_SECONDARY_A180})",
        "CARD_HOVER_BORDER": "1.8px solid {ACCENT}",
        "TIMER_BG":          "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {BG_PRIMARY_A160}, stop:0.5 {BG_SECONDARY_A160}, stop:1 {BG_PRIMARY_A160})",
        "TIMER_BORDER":      "1.5px solid {ACCENT}",
        "IDLE_BORDER":       "1.5px dashed {BORDER_COLOR}",
        "RADIUS":            "18px",
        "SUBJECT_RADIUS":    "20px",
        "TASK_RADIUS":       "16px",
    },
    "Space": {
        "CARD_BORDER_STYLE": "1px solid {TEXT_PRIMARY_A120}",
        "CARD_BG":           "{BG_SECONDARY_A140}",
        "CARD_HOVER_BG":     "{BG_TERTIARY_A170}",
        "CARD_HOVER_BORDER": "1.4px solid {TEXT_PRIMARY_A170}",
        "TIMER_BG":          "{BG_SECONDARY_A130}",
        "TIMER_BORDER":      "1px solid {ACCENT_A170}",
        "IDLE_BORDER":       "1px dashed {BORDER_COLOR_A150}",
        "RADIUS":            "20px",
        "SUBJECT_RADIUS":    "24px",
        "TASK_RADIUS":       "18px",
    },
    "Checkerboard": {
        "CARD_BORDER_STYLE": "2px solid {BORDER_COLOR_A220}",
        "CARD_BG":           "{BG_PRIMARY}",
        "CARD_HOVER_BG":     "{BG_SECONDARY}",
        "CARD_HOVER_BORDER": "2px solid {ACCENT}",
        "TIMER_BG":          "{BG_PRIMARY}",
        "TIMER_BORDER":      "2px solid {ACCENT}",
        "IDLE_BORDER":       "2px dashed {BORDER_COLOR}",
        "RADIUS":            "0px",
        "SUBJECT_RADIUS":    "0px",
        "TASK_RADIUS":       "0px",
    },
}

# ── Lists for UI ─────────────────────────────────────────────────────────────
PALETTE_NAMES = list(PALETTES.keys())
FX_NAMES = list(FX_STYLES.keys())

# ── Token resolver ───────────────────────────────────────────────────────────

def get_tokens(fx_name: str = "Glow", palette_name: str = "Ocean") -> dict:
    """
    Merge a colour palette with an FX style and return a flat dict
    of resolved string tokens ready for stylesheet interpolation.
    """
    palette = PALETTES.get(palette_name, PALETTES["Ocean"]).copy()

    # Precompute alpha variants for theme templates (e.g. BG_SECONDARY_A170).
    for key in ("BG_PRIMARY", "BG_SECONDARY", "BG_TERTIARY", "BORDER_COLOR", "TEXT_PRIMARY", "ACCENT"):
        base = palette.get(key)
        if not base:
            continue
        for alpha in (115, 120, 130, 140, 150, 155, 160, 170, 175, 180, 190, 195, 205, 220, 230):
            palette[f"{key}_A{alpha}"] = _rgba(base, alpha)

    fx_raw = FX_STYLES.get(fx_name, FX_STYLES["Glow"])

    # Resolve FX placeholders like {BG_SECONDARY} against the palette
    fx = {}
    for k, v in fx_raw.items():
        try:
            fx[k] = v.format(**palette)
        except KeyError:
            fx[k] = v

    tokens = {**palette, **fx}

    # Also add convenience radius tokens
    tokens.setdefault("RADIUS_SM", "6px")
    tokens.setdefault("RADIUS_MD", tokens.get("RADIUS", "10px"))
    tokens.setdefault("RADIUS_LG", "14px")

    return tokens
