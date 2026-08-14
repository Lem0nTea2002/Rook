from rook_seed.todos import render_todos


def test_render_todos_is_enabled_by_default() -> None:
    assert render_todos(["document release"]) == ["TODO: document release"]


def test_render_todos_can_be_disabled() -> None:
    assert render_todos(["document release"], include_todos=False) == []
