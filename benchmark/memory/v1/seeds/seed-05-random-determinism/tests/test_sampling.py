from rook_seed.sampling import sample_indices


def test_sample_indices_is_repeatable_for_a_fixed_seed() -> None:
    first = sample_indices(20, 5, random_state=7)
    second = sample_indices(20, 5, random_state=7)
    assert first == second


def test_sample_indices_can_use_another_seed() -> None:
    assert sample_indices(20, 5, random_state=3) != sample_indices(
        20, 5, random_state=7
    )
