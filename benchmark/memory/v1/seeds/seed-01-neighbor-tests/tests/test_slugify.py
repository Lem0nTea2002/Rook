from rook_seed.slugify import slugify


def test_slugify_simple_title() -> None:
    assert slugify("Night Flight") == "night-flight"


def test_slugify_collapses_consecutive_whitespace() -> None:
    assert slugify("  Night   Flight\tMint  ") == "night-flight-mint"
