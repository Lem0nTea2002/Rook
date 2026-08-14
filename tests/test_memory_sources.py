from __future__ import annotations

from collections import Counter

from rook_agent.benchmarks.memory_sources import (
    CHOICES,
    _validation_commands,
    choice_counts,
)


def test_memory_source_choices_have_two_tasks_per_confirmed_seed() -> None:
    seed_counts, repository_counts = choice_counts()

    assert len(CHOICES) == 20
    assert len({choice.instance_id for choice in CHOICES}) == 20
    assert set(seed_counts.values()) == {2}
    assert len(seed_counts) == 10
    assert repository_counts == Counter(
        {
            "django": 10,
            "matplotlib": 3,
            "astropy": 3,
            "pydata": 2,
            "pylint-dev": 1,
            "sympy": 1,
        }
    )


def test_memory_source_builds_repository_specific_validation_commands() -> None:
    django_command, django_regression = _validation_commands(
        {
            "instance_id": "django__django-1",
            "repo": "django/django",
            "FAIL_TO_PASS": '["test_case (app.tests.Case)"]',
            "test_patch": "",
        }
    )
    pytest_command, pytest_regression = _validation_commands(
        {
            "instance_id": "sympy__sympy-1",
            "repo": "sympy/sympy",
            "FAIL_TO_PASS": '["test_value"]',
            "test_patch": ("diff --git a/sympy/tests/test_value.py b/sympy/tests/test_value.py\n"),
        }
    )

    assert django_command[-1] == "app.tests.Case.test_case"
    assert django_regression[-1] == "app.tests"
    assert pytest_command[-1] == "sympy/tests/test_value.py::test_value"
    assert pytest_regression[-1] == "sympy/tests/test_value.py"


def test_memory_source_uses_django_module_for_descriptive_test_label() -> None:
    command, regression = _validation_commands(
        {
            "instance_id": "django__django-2",
            "repo": "django/django",
            "FAIL_TO_PASS": '["Descriptive test docstring."]',
            "test_patch": (
                "diff --git a/tests/forms_tests/tests/test_formsets.py "
                "b/tests/forms_tests/tests/test_formsets.py\n"
            ),
        }
    )

    assert command[-1] == "forms_tests.tests.test_formsets"
    assert regression == command
