# Upstream contribution batch 1: human review packet

Status: human review completed on 2026-07-28. The pytest and scikit-learn
patches were submitted as Draft PRs #14789 and #34587; the Sphinx patch
remains unsubmitted.

The three claim comments were published by `Lem0nTea2002`. The same account
must perform any later upstream submission so that the public issue and pull
request provenance remains unambiguous.

## pytest #14771

- Repository: `pytest-dev/pytest`
- Base: `70f8650a624c92f91c94dd26b391326df2ea4143`
- Branch: `rook/pytest-14771-frozen-docs`
- Reviewed local commit: `dc5731148730fb5aed15c3795507fff32affe3c0`
- Draft PR: <https://github.com/pytest-dev/pytest/pull/14789>
- Claim:
  <https://github.com/pytest-dev/pytest/issues/14771#issuecomment-5092887093>
- Changed files:
  - `doc/en/example/simple.rst`
  - `changelog/14771.doc.rst`

The patch documents the existing collection boundary instead of claiming that
pytest can collect test modules directly from a PyInstaller PYZ archive. It
explains that test sources must be exposed as filesystem data and provides a
PyInstaller `--add-data` example plus a filesystem path passed to
`pytest.main()`.

Validation:

- official `tox -e docs` build: passed with Sphinx `-W --keep-going`;
- 256 documentation sources read and rendered;
- targeted pre-commit hooks: passed;
- Towncrier draft: passed and placed #14771 under improved documentation;
- `git diff --check`: passed.

The completed human review covered:

1. the documentation-only scope is appropriate for the issue;
2. the PyInstaller data layout matches the intended frozen application;
3. the example accurately distinguishes importability from pytest's
   filesystem-based collection;
4. the contributor can explain and maintain the change.

## scikit-learn #13762

- Repository: `scikit-learn/scikit-learn`
- Base: `e5f607658ace73fef757b52ad65a2f74011a4a5f`
- Branch: `rook/sklearn-13762-stable-doctest`
- Reviewed local commit: `dd73a22cb25a48a04e121e9f50b752f9d424ab18`
- Draft PR: <https://github.com/scikit-learn/scikit-learn/pull/34587>
- Claim:
  <https://github.com/scikit-learn/scikit-learn/issues/13762#issuecomment-5092891285>
- Changed file: `sklearn/cluster/_bicluster.py`

The patch replaces an architecture-sensitive exact cluster-label expectation
with a strongly separated matrix and a label-permutation-invariant
row-to-column co-cluster relation. It removes both doctest skips while still
checking the meaningful clustering structure.

Validation:

- released scikit-learn 1.9.0 exploratory matrix: 20/20 combinations passed
  across two SVD backends and ten random seeds;
- current editable source build: scikit-learn `1.10.dev0`;
- standard-library runner against the current class docstring: 7/7 passed;
- `sklearn/cluster/tests/test_bicluster.py`: 21 passed, one warning;
- Ruff lint and format checks: passed;
- `git diff --check`: passed.

The repository's pytest configuration skips NumPy array doctests on Windows,
so that skip is recorded separately and is not reported as a passing pytest
doctest. The standard-library doctest runner executed the current docstring
without that project-level Windows skip.

The completed human review covered:

1. the new matrix is an appropriate public API example;
2. equality between row and column labels expresses the intended co-cluster
   relation without depending on arbitrary numeric label assignments;
3. a changelog fragment is added using the eventual pull request number, or
   the maintainers explicitly apply the `No Changelog Needed` policy;
4. the contributor understands why the original result differed across
   architectures.

## Sphinx #6689

- Repository: `sphinx-doc/sphinx`
- Base: `9af5b469df42c810c62453661c1974c0f254e674`
- Branch: `rook/sphinx-6689-inline-todo`
- Reviewed local commit: `0ff71a365a07fc399398b0d24932834c527f9c1e`
- Claim:
  <https://github.com/sphinx-doc/sphinx/issues/6689#issuecomment-5092896616>
- Changed files:
  - `sphinx/ext/todo.py`
  - `tests/test_extensions/test_ext_todo.py`
  - `tests/roots/test-ext-todo/foo.rst`
  - `doc/usage/extensions/todo.rst`
  - `CHANGES.rst`

The patch adds an inline todo node and role, collects it in the todo domain,
converts it to an admonition in `todolist`, preserves HTML and LaTeX backlinks,
emits the existing event and warnings, and hides it when
`todo_include_todos` is false.

Validation:

- extension integration tests: 3/3 passed;
- mypy for `sphinx/ext/todo.py`: passed;
- Ruff lint and format checks: passed;
- complete 155-source dummy documentation build with `-W --keep-going`:
  passed;
- `git diff --check`: passed.

The complete HTML documentation build read and rendered all 155 sources but
failed its strict final status because the host has no Graphviz `dot`
executable. No warning came from the modified todo source. This environment
limitation is not counted as a passing HTML build.

The completed human review covered:

1. inline rendering as `<span class="todo-inline">` is the desired public
   behavior;
2. converting an inline todo to a normal Todo admonition in `todolist` is the
   desired list representation;
3. the generated target identifiers and LaTeX hypertargets are appropriate;
4. the contributor can explain the domain collection, node visitors, deep
   copies, reference resolution, and configuration behavior.

Sphinx policy requires the human contributor to review and understand every
change, write the pull request description themselves, disclose the AI tool,
describe how it was used, identify AI-assisted code or text, and submit the
pull request manually.

This pull request includes code written with the assistance of AI.
The code has been reviewed by a human.
