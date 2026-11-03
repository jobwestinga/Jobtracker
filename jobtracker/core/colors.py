"""
Subject colour suggestions (pure, UI-free).

Suggests a few colours that are visually distinct from the colours already in use
by ACTIVE (non-archived) subjects, so a new subject is easy to tell apart on the
cards and in the graphs. This is intentionally simple — not a category/colour
system, no gradients.
"""

from __future__ import annotations

import colorsys
from typing import Iterable, List, Optional, Tuple

# A tasteful, well-spaced candidate palette to draw suggestions from. These match
# the preset swatches the dialog already offers plus a few extras for headroom.
DEFAULT_SUGGESTION_PALETTE: List[str] = [
    "#3B82F6",  # Blue
    "#22C55E",  # Green
    "#F59E0B",  # Amber
    "#EF4444",  # Red
    "#8B5CF6",  # Violet
    "#EC4899",  # Pink
    "#06B6D4",  # Cyan
    "#F97316",  # Orange
    "#84CC16",  # Lime
    "#14B8A6",  # Teal
    "#EAB308",  # Yellow
    "#A855F7",  # Purple
]

# Keep candidates in the same colourful, moderately bright family as the
# presets, with enough headroom when all eight presets are already in use.
DEFAULT_SUGGESTION_PALETTE += [
    "#" + "".join(f"{round(channel * 255):02X}" for channel in colorsys.hsv_to_rgb(hue / 360, saturation, value))
    for hue in range(0, 360, 10)
    for saturation in (0.6, 0.8, 0.95)
    for value in (0.8, 0.95)
]


def _hex_to_rgb(hex_color: str) -> Optional[Tuple[int, int, int]]:
    if not hex_color:
        return None
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return None
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return None


def _distance(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _oklab(rgb):
    """sRGB → perceptual coordinates (Björn Ottosson's public-domain matrices).

    https://bottosson.github.io/posts/oklab/#converting-from-linear-srgb-to-oklab
    """
    r, g, b = (c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _nearest_distance(candidate, others) -> float:
    if not others:
        return float("inf")
    return min(_distance(candidate, o) for o in others)


def suggest_colors(
    existing_colors: Iterable[str],
    palette: Optional[List[str]] = None,
    count: int = 3,
) -> List[str]:
    """Return up to ``count`` colours distinct from ``existing_colors``.

    Each pick maximizes its nearest Oklab distance to existing colours AND
    earlier picks. Recompute after each pick; deterministic palette-order ties.
    """
    if count <= 0:
        return []
    palette = DEFAULT_SUGGESTION_PALETTE if palette is None else palette
    existing = [rgb for c in existing_colors if (rgb := _hex_to_rgb(c)) is not None]
    occupied = [_oklab(rgb) for rgb in existing]
    candidates = {}
    for color in palette:
        rgb = _hex_to_rgb(color)
        if rgb is not None and rgb not in existing:
            candidates.setdefault(rgb, (color, _oklab(rgb)))
    picks = []
    while candidates and len(picks) < count:
        rgb = max(candidates, key=lambda rgb: _nearest_distance(candidates[rgb][1], occupied))
        color, lab = candidates.pop(rgb)
        picks.append(color)
        occupied.append(lab)
    return picks
