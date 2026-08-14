from rook_seed.normalizer import normalize_key


def test_normalize_key_uses_snake_case() -> None:
    assert normalize_key(" Night Flight ") == "night_flight"
