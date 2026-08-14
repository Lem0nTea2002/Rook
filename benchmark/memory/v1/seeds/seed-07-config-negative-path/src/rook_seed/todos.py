def render_todos(items: list[str], *, include_todos: bool = True) -> list[str]:
    return [f"TODO: {item}" for item in items]
