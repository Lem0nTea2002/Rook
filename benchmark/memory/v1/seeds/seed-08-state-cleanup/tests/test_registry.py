import pytest

from rook_seed.registry import REGISTRY, temporary_registration


def test_temporary_registration_is_visible_inside_context() -> None:
    with temporary_registration("html"):
        assert REGISTRY == ["html"]
    assert REGISTRY == []


def test_temporary_registration_cleans_up_after_failure() -> None:
    with pytest.raises(RuntimeError):
        with temporary_registration("latex"):
            raise RuntimeError("render failed")
    assert REGISTRY == []
