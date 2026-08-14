import random


def sample_indices(size: int, count: int) -> list[int]:
    return random.sample(range(size), count)
