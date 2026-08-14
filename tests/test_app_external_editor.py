from pathlib import Path

from rook_agent.app.external_editor import ExternalEditorService


def test_external_editor_uses_visual_and_returns_edited_text() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> int:
        commands.append(command)
        Path(command[-1]).write_text("edited prompt", encoding="utf-8")
        return 0

    service = ExternalEditorService(
        env={"VISUAL": "code --wait"},
        runner=runner,
        platform_name="posix",
    )

    result = service.edit("initial")

    assert result.ok is True
    assert result.text == "edited prompt"
    assert commands[0][:2] == ["code", "--wait"]
    assert not Path(commands[0][-1]).exists()


def test_external_editor_reports_missing_configuration() -> None:
    result = ExternalEditorService(env={}).edit("keep me")

    assert result.ok is False
    assert result.text == "keep me"
    assert "$VISUAL" in (result.error or "")
