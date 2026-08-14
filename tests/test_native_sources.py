from __future__ import annotations

from collections import Counter

import pytest

from rook_agent.benchmarks.native_sources import (
    CHOICES,
    _json_string_list,
    _pytest_file_command,
)


def test_native_source_choices_match_v1_quotas() -> None:
    repositories = Counter(
        item.instance_id.split("__", 1)[0] for item in CHOICES
    )
    categories = Counter(item.category for item in CHOICES)

    assert repositories == {
        "pytest-dev": 10,
        "scikit-learn": 10,
        "sphinx-doc": 10,
    }
    assert categories == {
        "bug": 12,
        "test": 6,
        "documentation": 4,
        "refactor": 4,
        "compatibility": 4,
    }
    assert len({item.instance_id for item in CHOICES}) == 30


def test_native_regression_command_deduplicates_test_files() -> None:
    command = _pytest_file_command(
        [
            "tests/test_a.py::test_one",
            "tests/test_a.py::test_two",
            "tests/test_b.py::test_three",
        ]
    )

    assert command[-2:] == ("tests/test_a.py", "tests/test_b.py")


def test_native_dataset_test_list_rejects_non_string_values() -> None:
    with pytest.raises(ValueError, match="必须是非空字符串列表"):
        _json_string_list(
            '["test_ok", 1]',
            field="FAIL_TO_PASS",
            instance_id="repo__name-1",
        )
