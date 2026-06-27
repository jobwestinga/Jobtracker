"""Subject colour-suggestion algorithm."""

from jobtracker.core import colors


def test_returns_requested_count():
    assert len(colors.suggest_colors([], count=3)) == 3


def test_suggestions_are_distinct():
    picks = colors.suggest_colors([], count=3)
    assert len(set(picks)) == 3


def test_avoids_colors_close_to_existing():
    # An exact existing blue should not be the top suggestion.
    existing = ["#3B82F6"]  # Blue, also in the palette
    picks = colors.suggest_colors(existing, count=3)
    assert "#3B82F6" not in picks


def test_suggestions_are_far_from_all_existing():
    existing = ["#3B82F6", "#22C55E"]  # blue + green
    picks = colors.suggest_colors(existing, count=3)
    existing_rgb = [colors._hex_to_rgb(c) for c in existing]
    for pick in picks:
        prgb = colors._hex_to_rgb(pick)
        nearest = min(colors._distance(prgb, e) for e in existing_rgb)
        assert nearest >= colors._MIN_PICK_DISTANCE


def test_archived_colors_not_passed_in_are_ignored():
    # Caller passes only active subject colours; the algorithm just trusts input.
    picks = colors.suggest_colors(["#000000"], count=2)
    assert len(picks) == 2


def test_handles_invalid_existing_colors():
    picks = colors.suggest_colors(["not-a-color", "", None], count=3)
    assert len(picks) == 3


def test_small_palette_still_fills_count():
    picks = colors.suggest_colors([], palette=["#3B82F6", "#22C55E"], count=3)
    # Only two distinct candidates exist; must not crash, returns what it can.
    assert len(picks) == 2
