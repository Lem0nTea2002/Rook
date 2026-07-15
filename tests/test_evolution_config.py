from __future__ import annotations

import pytest

from rook_agent.config import AppConfig
from rook_agent.evolution import EvolutionConfig, EvolutionScope, load_evolution_config


def test_evolution_defaults_to_disabled() -> None:
    config = AppConfig(provider_name="openai", env={})

    assert load_evolution_config(config) == EvolutionConfig()


def test_project_evolution_config_overrides_global() -> None:
    config = AppConfig(
        provider_name="openai",
        env={},
        project_config={"evolution": {"enabled": True, "allow_global": False}},
        global_config={"evolution": {"enabled": False, "allow_global": True}},
    )

    evolution = load_evolution_config(config)

    assert evolution.enabled is True
    assert evolution.allow_global is False


def test_project_false_and_zero_values_are_not_treated_as_missing() -> None:
    config = AppConfig(
        provider_name="openai",
        env={},
        project_config={
            "evolution": {
                "enabled": False,
                "allow_global": 0,
                "max_skills_per_task": 1,
            }
        },
        global_config={
            "evolution": {
                "enabled": True,
                "allow_global": True,
                "max_skills_per_task": 2,
            }
        },
    )

    evolution = load_evolution_config(config)

    assert evolution.enabled is False
    assert evolution.allow_global is False
    assert evolution.max_skills_per_task == 1


@pytest.mark.parametrize("scope", list(EvolutionScope))
def test_evolution_accepts_each_supported_scope(scope: EvolutionScope) -> None:
    config = AppConfig(
        provider_name="openai",
        env={},
        project_config={"evolution": {"scope": scope.value}},
    )

    assert load_evolution_config(config).scope is scope


def test_evolution_rejects_invalid_scope() -> None:
    config = AppConfig(
        provider_name="openai",
        env={},
        project_config={"evolution": {"scope": "workspace"}},
    )

    with pytest.raises(
        ValueError,
        match="invalid evolution scope: 'workspace'; expected one of: auto, project, global",
    ):
        load_evolution_config(config)


@pytest.mark.parametrize("count", [0, 3])
def test_evolution_rejects_count_outside_supported_range(count: int) -> None:
    config = AppConfig(
        provider_name="openai",
        env={},
        project_config={"evolution": {"max_skills_per_task": count}},
    )

    with pytest.raises(
        ValueError,
        match=f"max_skills_per_task must be between 1 and 2, got {count}",
    ):
        load_evolution_config(config)
