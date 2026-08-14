from rook_agent.app.prompt_history import PromptHistoryStore


def test_prompt_history_persists_chat_but_not_shell_or_slash(tmp_path) -> None:
    store = PromptHistoryStore(tmp_path)

    store.append("review the current change")
    store.append("!echo secret")
    store.append("/status")

    assert [entry.text for entry in store.load()] == ["review the current change"]


def test_prompt_history_is_bounded_deduplicated_and_searchable(tmp_path) -> None:
    store = PromptHistoryStore(tmp_path, max_entries=2)

    store.append("first prompt")
    store.append("second prompt")
    store.append("second prompt")
    store.append("third prompt")

    assert [entry.text for entry in store.load()] == ["second prompt", "third prompt"]
    assert [entry.text for entry in store.search("second")] == ["second prompt"]
