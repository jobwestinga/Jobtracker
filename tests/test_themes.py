"""Theme choices and token resolution."""

from jobtracker.core.themes import FX_NAMES, PALETTE_NAMES, get_tokens


def test_explicit_light_and_dark_palettes_are_available():
    assert "Light" in PALETTE_NAMES
    assert "Dark" in PALETTE_NAMES
    light = get_tokens("Base", "Light")
    dark = get_tokens("Base", "Dark")
    assert light["BG_PRIMARY"] != dark["BG_PRIMARY"]
    assert light["TEXT_PRIMARY"] != dark["TEXT_PRIMARY"]


def test_base_fx_is_flat_and_opaque():
    assert "Base" in FX_NAMES
    tokens = get_tokens("Base", "Ocean")
    assert tokens["CARD_BG"] == tokens["BG_SECONDARY"]
    assert "gradient" not in tokens["CARD_BG"]
