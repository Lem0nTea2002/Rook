from rook_agent.app.clipboard import ClipboardService


class FakeProcessRunner:
    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], text: str):
        self.calls.append((argv, text))
        return type("Completed", (), {"returncode": self.returncode, "stderr": self.stderr})()


def test_clipboard_uses_terminal_and_confirmed_windows_fallback() -> None:
    terminal_values: list[str] = []
    runner = FakeProcessRunner()
    service = ClipboardService(platform_name="win32", process_runner=runner)

    result = service.copy("hello", terminal_copy=terminal_values.append)

    assert result.ok is True
    assert result.backend == "clip.exe"
    assert terminal_values == ["hello"]
    assert runner.calls == [(["clip.exe"], "hello")]


def test_clipboard_reports_terminal_fallback_when_native_backend_is_missing() -> None:
    terminal_values: list[str] = []
    service = ClipboardService(
        platform_name="linux",
        command_finder=lambda command: None,
        process_runner=FakeProcessRunner(),
    )

    result = service.copy("hello", terminal_copy=terminal_values.append)

    assert result.ok is True
    assert result.backend == "terminal-osc52"
    assert terminal_values == ["hello"]


def test_clipboard_does_not_claim_success_when_every_backend_fails() -> None:
    runner = FakeProcessRunner(returncode=1, stderr="clipboard unavailable")
    service = ClipboardService(platform_name="win32", process_runner=runner)

    result = service.copy("hello")

    assert result.ok is False
    assert result.backend is None
    assert result.error == "clipboard unavailable"


def test_clipboard_rejects_empty_text() -> None:
    result = ClipboardService(platform_name="linux").copy("")

    assert result.ok is False
    assert result.error == "没有可复制的内容"
