from pathlib import Path

from rook_seed.prefix import without_prefix


def test_without_prefix_preserves_behavior() -> None:
    assert without_prefix("rook-agent", "rook-") == "agent"
    assert without_prefix("agent", "rook-") == "agent"


def test_implementation_supports_declared_python_38() -> None:
    source = (Path(__file__).parents[1] / "src" / "rook_seed" / "prefix.py").read_text(
        encoding="utf-8"
    )
    assert ".removeprefix(" not in source
