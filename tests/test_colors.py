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


def test_each_pick_is_farthest_from_existing_and_previous_picks():
    existing = ["#3B82F6", "#22C55E"]
    picks = colors.suggest_colors(existing, count=4)
    occupied = [colors._oklab(colors._hex_to_rgb(c)) for c in existing]
    remaining = set(colors.DEFAULT_SUGGESTION_PALETTE) - set(existing)
    for pick in picks:
        lab = colors._oklab(colors._hex_to_rgb(pick))
        score = colors._nearest_distance(lab, occupied)
        assert all(score >= colors._nearest_distance(colors._oklab(colors._hex_to_rgb(c)), occupied) - 1e-12 for c in remaining)
        remaining.remove(pick)
        occupied.append(lab)


def test_many_subjects_still_receive_four_unused_colors():
    existing = colors.DEFAULT_SUGGESTION_PALETTE[:12]
    picks = colors.suggest_colors(existing, count=4)
    assert len(set(picks)) == 4
    assert not set(picks) & set(existing)
    assert picks == colors.suggest_colors(reversed(existing), count=4)


def test_empty_duplicate_and_invalid_candidates():
    assert colors.suggest_colors([], palette=[], count=4) == []
    assert colors.suggest_colors([], count=0) == []
    assert colors.suggest_colors([], palette=["#abcdef", "#ABCDEF", "bad"], count=4) == ["#abcdef"]


def test_oklab_reference_primaries():
    import pytest
    assert colors._oklab((255, 0, 0)) == pytest.approx((0.627955, 0.224863, 0.125846), abs=1e-6)
    assert colors._oklab((255, 255, 255)) == pytest.approx((1, 0, 0), abs=1e-6)


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
