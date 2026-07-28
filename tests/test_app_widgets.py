from rook_agent.app.widgets import PermissionCard, ToolCard


class FakeClick:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_tool_card_collapses_and_expands_long_output() -> None:
    card = ToolCard("x" * 500, header="shell · success", classes="tool-done")
    event = FakeClick()

    assert card.expanded is False
    assert "▸" in str(card.content)

    card.on_click(event)

    assert card.expanded is True
    assert event.stopped is True
    assert "▾" in str(card.content)


def test_permission_card_is_selectable() -> None:
    card = PermissionCard("allow?", header="permission", classes="permission-message")

    assert card.ALLOW_SELECT is True
