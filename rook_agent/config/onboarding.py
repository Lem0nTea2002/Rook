"""Interactive first-run Provider setup."""

from __future__ import annotations

from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Protocol
from urllib.parse import urlparse

from rook_agent.config.credentials import read_api_key, write_api_key
from rook_agent.config.settings import AppConfig, default_global_config_path, load_config
from rook_agent.providers.presets import PROVIDER_PRESETS, ProviderPreset


CUSTOM_PROVIDER = "openai-compatible"
PROVIDER_CHOICES = (
    "openai",
    "deepseek",
    "qwen",
    "moonshot",
    "zhipu",
    "openrouter",
    "anthropic",
    "ollama",
    "custom",
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SetupPrompter(Protocol):
    def ask(self, prompt: str) -> str:
        ...

    def secret(self, prompt: str) -> str:
        ...

    def tell(self, message: str) -> None:
        ...


class ConsoleSetupPrompter:
    def ask(self, prompt: str) -> str:
        return input(prompt)

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def tell(self, message: str) -> None:
        print(message)


@dataclass(frozen=True, slots=True)
class SetupResult:
    provider_name: str
    model: str
    api_key_env: str | None
    config_path: Path
    credential_persisted: bool
    reused_config: bool


@dataclass(frozen=True, slots=True)
class _ProviderSelection:
    provider_name: str
    provider_display_name: str
    model: str
    base_url: str | None
    api_key_env: str | None


def run_setup_wizard(
    *,
    project_root: Path | str,
    provider_name: str | None = None,
    force: bool = False,
    env: dict[str, str] | None = None,
    prompter: SetupPrompter | None = None,
    credential_reader: Callable[[str], str | None] = read_api_key,
    credential_writer: Callable[[str, str], None] = write_api_key,
) -> SetupResult:
    """Collect Provider settings without making a model or network call."""

    prompt = prompter or ConsoleSetupPrompter()
    environment = dict(os.environ if env is None else env)
    existing = load_config(provider_name, project_root=project_root, env=environment)
    prompt.tell("")
    prompt.tell("Rook first-run setup")
    prompt.tell("No model request will be sent during setup.")

    if existing.loaded_config_paths and not force:
        selection = _selection_from_existing(existing)
        existing_path = existing.project_config_path or existing.global_config_path
        if existing_path is None:
            raise RuntimeError("loaded configuration has no source path")
        result = _configure_credential(
            selection,
            environment=environment,
            config_path=existing_path,
            reused_config=True,
            prompt=prompt,
            credential_reader=credential_reader,
            credential_writer=credential_writer,
            replace_stored_credential=False,
        )
        prompt.tell(f"Using existing configuration: {result.config_path}")
        return result

    selection = _collect_provider_selection(
        prompt,
        preselected=provider_name,
    )
    config_path = default_global_config_path()
    result = _configure_credential(
        selection,
        environment=environment,
        config_path=config_path,
        reused_config=False,
        prompt=prompt,
        credential_reader=credential_reader,
        credential_writer=credential_writer,
        replace_stored_credential=force,
    )
    _write_config_atomically(
        config_path,
        _render_config(selection),
        overwrite=force,
    )
    prompt.tell(f"Configuration saved: {config_path}")
    if result.credential_persisted:
        prompt.tell("API key saved in the operating-system credential manager.")
    elif selection.api_key_env:
        prompt.tell(f"Using API key from environment variable {selection.api_key_env}.")
    else:
        prompt.tell("This local Provider does not require an API key.")
    return result


def _collect_provider_selection(
    prompt: SetupPrompter,
    *,
    preselected: str | None,
) -> _ProviderSelection:
    provider = _normalized_preselection(preselected)
    if provider is None:
        prompt.tell("Choose a model Provider:")
        labels = {
            "openai": "OpenAI API",
            "deepseek": "DeepSeek",
            "qwen": "Qwen / DashScope",
            "moonshot": "Moonshot",
            "zhipu": "Zhipu",
            "openrouter": "OpenRouter",
            "anthropic": "Anthropic",
            "ollama": "Ollama (local, no API key)",
            "custom": "Custom OpenAI-compatible endpoint",
        }
        for index, name in enumerate(PROVIDER_CHOICES, start=1):
            prompt.tell(f"  {index}. {labels[name]}")
        provider = _ask_choice(prompt, "Provider [1]: ", PROVIDER_CHOICES, default_index=1)

    if provider == "custom":
        return _collect_custom_selection(prompt)
    preset = PROVIDER_PRESETS[provider]
    model = _ask_with_default(prompt, f"Model [{preset.default_model}]: ", preset.default_model)
    base_url = _collect_preset_base_url(prompt, preset)
    return _ProviderSelection(
        provider_name=provider,
        provider_display_name=provider,
        model=model,
        base_url=base_url,
        api_key_env=None if provider == "ollama" else preset.api_key_env,
    )


def _collect_custom_selection(prompt: SetupPrompter) -> _ProviderSelection:
    name = _ask_required(prompt, "Provider name (for example acme): ").lower()
    if not _PROVIDER_NAME.fullmatch(name):
        raise ValueError("Provider name may contain only letters, digits, '.', '_', and '-'")
    base_url = _validate_base_url(_ask_required(prompt, "OpenAI-compatible Base URL: "))
    model = _ask_required(prompt, "Model name: ")
    api_key_env = _ask_with_default(prompt, "API-key environment name [ROOK_API_KEY]: ", "ROOK_API_KEY")
    if not _ENV_NAME.fullmatch(api_key_env):
        raise ValueError("API-key environment name must be a valid environment variable name")
    return _ProviderSelection(
        provider_name=CUSTOM_PROVIDER,
        provider_display_name=name,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
    )


def _collect_preset_base_url(prompt: SetupPrompter, preset: ProviderPreset) -> str | None:
    if preset.kind == "anthropic":
        return None
    default = preset.default_base_url or ""
    label = f"Base URL [{default or 'Provider default'}]: "
    value = prompt.ask(label).strip()
    if not value:
        return default or None
    return _validate_base_url(value)


def _configure_credential(
    selection: _ProviderSelection,
    *,
    environment: dict[str, str],
    config_path: Path,
    reused_config: bool,
    prompt: SetupPrompter,
    credential_reader: Callable[[str], str | None],
    credential_writer: Callable[[str, str], None],
    replace_stored_credential: bool,
) -> SetupResult:
    key_env = selection.api_key_env
    persisted = False
    if key_env:
        environment_key = environment.get(key_env)
        stored_key = None if environment_key else credential_reader(key_env)
        if not environment_key and (replace_stored_credential or not stored_key):
            value = prompt.secret(f"{key_env} (input hidden): ").strip()
            if not value:
                raise ValueError("API key must not be empty")
            credential_writer(key_env, value)
            persisted = True
    return SetupResult(
        provider_name=selection.provider_name,
        model=selection.model,
        api_key_env=key_env,
        config_path=config_path,
        credential_persisted=persisted,
        reused_config=reused_config,
    )


def _selection_from_existing(config: AppConfig) -> _ProviderSelection:
    provider = config.provider_name
    if provider in {CUSTOM_PROVIDER, "custom"}:
        display_name = config.get_provider_value("name", default="openai-compatible") or "openai-compatible"
        model = _model_from_ref(config.get_config_value("model"), provider_name=display_name)
        if not model:
            raise ValueError("existing custom Provider is missing a model")
        base_url = config.get_provider_value("base_url", provider_name=display_name)
        if not base_url:
            raise ValueError("existing custom Provider is missing a Base URL")
        return _ProviderSelection(
            provider_name=CUSTOM_PROVIDER,
            provider_display_name=display_name,
            model=model,
            base_url=base_url,
            api_key_env=config.get_provider_value("api_key_env", provider_name=display_name)
            or "ROOK_API_KEY",
        )
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        raise ValueError(f"unsupported Provider in existing configuration: {provider}")
    model = _model_from_ref(config.get_config_value("model"), provider_name=provider)
    return _ProviderSelection(
        provider_name=provider,
        provider_display_name=provider,
        model=model or preset.default_model,
        base_url=config.get_provider_value("base_url", provider_name=provider)
        or preset.default_base_url,
        api_key_env=None
        if provider == "ollama"
        else config.get_provider_value("api_key_env", provider_name=provider)
        or preset.api_key_env,
    )


def _model_from_ref(value: str | None, *, provider_name: str) -> str | None:
    if not value:
        return None
    prefix = f"{provider_name}/"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _render_config(selection: _ProviderSelection) -> str:
    model_ref = f"{selection.provider_display_name}/{selection.model}"
    lines = [
        "# Rook global configuration generated by `rook config setup`.",
        f"model = {_toml_string(model_ref)}",
        "",
        "[provider]",
        f"type = {_toml_string(selection.provider_name)}",
    ]
    if selection.provider_name == CUSTOM_PROVIDER:
        lines.append(f"name = {_toml_string(selection.provider_display_name)}")
    if selection.base_url:
        lines.append(f"base_url = {_toml_string(selection.base_url)}")
    if selection.api_key_env:
        lines.append(f"api_key_env = {_toml_string(selection.api_key_env)}")
    lines.extend(
        [
            "parallel_tool_calls = false",
            "",
            "[permissions]",
            'mode = "ask"',
            "",
            "[ui]",
            'theme = "default"',
            "",
        ]
    )
    return "\n".join(lines)


def _write_config_atomically(path: Path, content: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"configuration already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalized_preselection(provider: str | None) -> str | None:
    if provider is None:
        return None
    value = provider.strip().lower()
    if value == CUSTOM_PROVIDER:
        return "custom"
    if value not in PROVIDER_CHOICES:
        raise ValueError(f"unsupported Provider for setup: {provider}")
    return value


def _ask_choice(
    prompt: SetupPrompter,
    message: str,
    choices: tuple[str, ...],
    *,
    default_index: int,
) -> str:
    raw = prompt.ask(message).strip()
    if not raw:
        return choices[default_index - 1]
    if raw.isdigit() and 1 <= int(raw) <= len(choices):
        return choices[int(raw) - 1]
    normalized = raw.lower()
    if normalized in choices:
        return normalized
    raise ValueError(f"invalid Provider choice: {raw}")


def _ask_with_default(prompt: SetupPrompter, message: str, default: str) -> str:
    return prompt.ask(message).strip() or default


def _ask_required(prompt: SetupPrompter, message: str) -> str:
    value = prompt.ask(message).strip()
    if not value:
        raise ValueError("value must not be empty")
    return value


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("Base URL must not contain credentials")
    return value.rstrip("/")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
