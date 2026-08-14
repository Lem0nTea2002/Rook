from rook_agent.app.clipboard import ClipboardService


class FakeProcessRunner:
    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], text: str):
        self.calls.append((argv, text))
        return type("Completed", (), {"returncode": self.returncode, "stderr": self.stderr})()


class FakeWindowsWriter:
    def __init__(self, *, error: OSError | None = None) -> None:
        self.error = error
        self.values: list[str] = []

    def __call__(self, text: str) -> None:
        self.values.append(text)
        if self.error is not None:
            raise self.error


def test_clipboard_uses_native_unicode_windows_backend() -> None:
    terminal_values: list[str] = []
    runner = FakeProcessRunner()
    writer = FakeWindowsWriter()
    service = ClipboardService(
        platform_name="win32",
        process_runner=runner,
        windows_writer=writer,
    )

    result = service.copy("中文标题", terminal_copy=terminal_values.append)

    assert result.ok is True
    assert result.backend == "win32-clipboard"
    assert terminal_values == ["中文标题"]
    assert writer.values == ["中文标题"]
    assert runner.calls == []


def test_clipboard_falls_back_to_terminal_when_windows_backend_fails() -> None:
    terminal_values: list[str] = []
    writer = FakeWindowsWriter(error=OSError("clipboard busy"))
    service = ClipboardService(platform_name="win32", windows_writer=writer)

    result = service.copy("中文标题", terminal_copy=terminal_values.append)

    assert result.ok is True
    assert result.backend == "terminal-osc52"
    assert terminal_values == ["中文标题"]


def test_clipboard_reports_windows_backend_failure_without_terminal_fallback() -> None:
    writer = FakeWindowsWriter(error=OSError("clipboard busy"))
    service = ClipboardService(platform_name="win32", windows_writer=writer)

    result = service.copy("中文标题")

    assert result.ok is False
    assert result.backend is None
    assert result.error == "clipboard busy"


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
    service = ClipboardService(
        platform_name="win32",
        windows_writer=FakeWindowsWriter(error=OSError("clipboard unavailable")),
    )

    result = service.copy("hello")

    assert result.ok is False
    assert result.backend is None
    assert result.error == "clipboard unavailable"


def test_clipboard_rejects_empty_text() -> None:
    result = ClipboardService(platform_name="linux").copy("")

    assert result.ok is False
    assert result.error == "没有可复制的内容"
