from rook_seed.formatting import normalize_name


def test_normalize_name_contract() -> None:
    assert normalize_name(" Rook ") == "rook"
