from statistics import quantiles
from time import perf_counter

from rook_agent.app.command_registry import CommandRegistry, CommandSpec
from rook_agent.app.commands import CommandResult
from rook_agent.app.tui_state import TuiEntryKind, TuiTranscript


class NoopHandler:
    def handle(self, text: str) -> CommandResult:
        return CommandResult(handled=True)


def test_five_thousand_entry_state_append_stays_under_fifty_ms() -> None:
    transcript = TuiTranscript()

    started = perf_counter()
    for index in range(5_000):
        transcript.add(TuiEntryKind.SYSTEM, f"entry {index}")
    elapsed_ms = (perf_counter() - started) * 1_000

    assert len(transcript.entries) == 5_000
    assert len(transcript.visible_entries(200)) == 200
    assert transcript.visible_entries(200)[0].body == "entry 4800"
    assert elapsed_ms < 50


def test_command_search_p95_stays_under_one_hundred_ms() -> None:
    registry = CommandRegistry()
    handler = NoopHandler()
    for index in range(500):
        registry.register(
            CommandSpec(f"/command-{index}", f"测试命令 {index}", "性能"),
            handler,
        )

    samples = []
    for _ in range(30):
        started = perf_counter()
        registry.suggest("/command-49", limit=8)
        samples.append((perf_counter() - started) * 1_000)

    assert quantiles(samples, n=20)[18] < 100
