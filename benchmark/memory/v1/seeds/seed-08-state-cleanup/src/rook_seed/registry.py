from collections.abc import Iterator
from contextlib import contextmanager


REGISTRY: list[str] = []


@contextmanager
def temporary_registration(name: str) -> Iterator[None]:
    REGISTRY.append(name)
    yield
    REGISTRY.remove(name)
