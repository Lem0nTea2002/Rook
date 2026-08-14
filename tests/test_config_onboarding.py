from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rook_agent.config.onboarding import SetupResult, run_setup_wizard


@dataclass
class ScriptedPrompter:
    answers: list[str]
    secrets: list[str]
    messages: list[str] = field(default_factory=list)

    def ask(self, prompt: str) -> str:
        self.messages.append(prompt)
        return self.answers.pop(0)

    def secret(self, prompt: str) -> str:
        self.messages.append(prompt)
        return self.secrets.pop(0)

    def tell(self, message: str) -> None:
        self.messages.append(message)


def test_first_run_setup_writes_keyless_openai_config_and_system_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    stored: list[tuple[str, str]] = []
    prompter = ScriptedPrompter(
        answers=["1", "", ""],
        secrets=["test-secret-value"],
    )

    result = run_setup_wizard(
        project_root=tmp_path,
        env={},
        prompter=prompter,
        credential_writer=lambda name, value: stored.append((name, value)),
    )

    config_path = tmp_path / "xdg" / "rook" / "config.toml"
    content = config_path.read_text(encoding="utf-8")
    assert result == SetupResult(
        provider_name="openai",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        config_path=config_path,
        credential_persisted=True,
        reused_config=False,
    )
    assert stored == [("OPENAI_API_KEY", "test-secret-value")]
    assert 'model = "openai/gpt-4.1-mini"' in content
    assert 'type = "openai"' in content
    assert 'api_key_env = "OPENAI_API_KEY"' in content
    assert "test-secret-value" not in content


def test_first_run_setup_supports_custom_openai_compatible_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    stored: list[tuple[str, str]] = []
    prompter = ScriptedPrompter(
        answers=[
            "9",
            "acme",
            "https://llm.example.com/v1",
            "coder-large",
            "ACME_API_KEY",
        ],
        secrets=["custom-secret"],
    )

    result = run_setup_wizard(
        project_root=tmp_path,
        env={},
        prompter=prompter,
        credential_writer=lambda name, value: stored.append((name, value)),
    )

    content = result.config_path.read_text(encoding="utf-8")
    assert result.provider_name == "openai-compatible"
    assert result.model == "coder-large"
    assert result.api_key_env == "ACME_API_KEY"
    assert stored == [("ACME_API_KEY", "custom-secret")]
    assert 'name = "acme"' in content
    assert 'base_url = "https://llm.example.com/v1"' in content
    assert 'model = "acme/coder-large"' in content
    assert "custom-secret" not in content


def test_setup_reuses_existing_config_and_only_collects_missing_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_path = tmp_path / "xdg" / "rook" / "config.toml"
    config_path.parent.mkdir(parents=True)
    original = "\n".join(
        [
            'model = "deepseek/deepseek-coder"',
            "[provider]",
            'type = "deepseek"',
            'api_key_env = "DEEPSEEK_API_KEY"',
            "",
        ]
    )
    config_path.write_text(original, encoding="utf-8")
    stored: list[tuple[str, str]] = []
    prompter = ScriptedPrompter(answers=[], secrets=["deepseek-secret"])

    result = run_setup_wizard(
        project_root=tmp_path,
        env={},
        prompter=prompter,
        credential_reader=lambda name: None,
        credential_writer=lambda name, value: stored.append((name, value)),
    )

    assert result.reused_config is True
    assert result.provider_name == "deepseek"
    assert result.model == "deepseek-coder"
    assert stored == [("DEEPSEEK_API_KEY", "deepseek-secret")]
    assert config_path.read_text(encoding="utf-8") == original


def test_setup_uses_detected_environment_key_without_persisting_a_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    stored: list[tuple[str, str]] = []
    prompter = ScriptedPrompter(
        answers=["2", "", ""],
        secrets=[],
    )

    result = run_setup_wizard(
        project_root=tmp_path,
        env={"DEEPSEEK_API_KEY": "from-environment"},
        prompter=prompter,
        credential_writer=lambda name, value: stored.append((name, value)),
    )

    assert result.provider_name == "deepseek"
    assert result.credential_persisted is False
    assert stored == []
    assert prompter.secrets == []


def test_force_setup_replaces_existing_system_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    stored: list[tuple[str, str]] = []
    prompter = ScriptedPrompter(
        answers=["deepseek-v4-flash", ""],
        secrets=["rotated-secret"],
    )

    result = run_setup_wizard(
        project_root=tmp_path,
        provider_name="deepseek",
        force=True,
        env={},
        prompter=prompter,
        credential_reader=lambda _name: "stale-secret",
        credential_writer=lambda name, value: stored.append((name, value)),
    )

    assert result.credential_persisted is True
    assert stored == [("DEEPSEEK_API_KEY", "rotated-secret")]
    assert prompter.secrets == []


def test_setup_never_calls_a_model_or_network_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    prompter = ScriptedPrompter(
        answers=["8", "", ""],
        secrets=[],
    )

    result = run_setup_wizard(
        project_root=tmp_path,
        env={},
        prompter=prompter,
        credential_writer=lambda _name, _value: (_ for _ in ()).throw(
            AssertionError("Ollama must not store a credential")
        ),
    )

    assert result.provider_name == "ollama"
    assert result.api_key_env is None
    assert result.credential_persisted is False


def test_setup_does_not_publish_config_when_credential_storage_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    prompter = ScriptedPrompter(
        answers=["1", "", ""],
        secrets=["secret-that-must-not-leak"],
    )

    with pytest.raises(RuntimeError, match="credential backend unavailable"):
        run_setup_wizard(
            project_root=tmp_path,
            env={},
            prompter=prompter,
            credential_writer=lambda _name, _value: (_ for _ in ()).throw(
                RuntimeError("credential backend unavailable")
            ),
        )

    config_path = tmp_path / "xdg" / "rook" / "config.toml"
    assert not config_path.exists()


def test_custom_setup_rejects_provider_names_that_break_model_references(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    prompter = ScriptedPrompter(
        answers=["9", "bad/provider"],
        secrets=[],
    )

    with pytest.raises(ValueError, match="Provider name"):
        run_setup_wizard(
            project_root=tmp_path,
            env={},
            prompter=prompter,
            credential_writer=lambda _name, _value: None,
        )
