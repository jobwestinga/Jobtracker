"""
Subject colour suggestions (pure, UI-free).

Suggests a few colours that are visually distinct from the colours already in use
by ACTIVE (non-archived) subjects, so a new subject is easy to tell apart on the
cards and in the graphs. This is intentionally simple — not a category/colour
system, no gradients.
"""

from __future__ import annotations

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

# Minimum RGB euclidean distance for two suggestions to count as "distinct".
_MIN_PICK_DISTANCE = 60.0


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


def _distance(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _nearest_distance(candidate: Tuple[int, int, int], others: List[Tuple[int, int, int]]) -> float:
    if not others:
        return float("inf")
    return min(_distance(candidate, o) for o in others)


def suggest_colors(
    existing_colors: Iterable[str],
    palette: Optional[List[str]] = None,
    count: int = 3,
) -> List[str]:
    """Return up to ``count`` colours distinct from ``existing_colors``.

    Candidates are ranked by how far their nearest existing colour is (farther =
    better), then greedily picked while staying distinct from already-picked
    suggestions. Deterministic for a given input.
    """
    palette = palette or DEFAULT_SUGGESTION_PALETTE
    existing_rgb = [rgb for rgb in (_hex_to_rgb(c) for c in existing_colors) if rgb is not None]

    candidates = []
    for hex_color in palette:
        rgb = _hex_to_rgb(hex_color)
        if rgb is None:
            continue
        candidates.append((hex_color, rgb, _nearest_distance(rgb, existing_rgb)))

    # Farthest-from-existing first; stable on ties by palette order.
    candidates.sort(key=lambda item: item[2], reverse=True)

    picks: List[str] = []
    picks_rgb: List[Tuple[int, int, int]] = []
    for hex_color, rgb, _score in candidates:
        if all(_distance(rgb, p) >= _MIN_PICK_DISTANCE for p in picks_rgb):
            picks.append(hex_color)
            picks_rgb.append(rgb)
        if len(picks) >= count:
            break

    # If distinctness filtering left us short (tiny palette), top up by score.
    if len(picks) < count:
        for hex_color, _rgb, _score in candidates:
            if hex_color not in picks:
                picks.append(hex_color)
            if len(picks) >= count:
                break

    return picks[:count]
