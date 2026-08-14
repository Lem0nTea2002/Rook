# Rook Complete Rename Design

## Decision

The product brand is **Rook**. The brand never carries a suffix; suffixes are
used only where package ecosystems require an unambiguous technical name.

## Canonical identifiers

| Surface | Identifier |
| --- | --- |
| Product and UI | `Rook` |
| Project subtitle | `Local Coding Agent` |
| Python distribution | `rook-agent` |
| Python import package | `rook_agent` |
| Console command | `rook` |
| Module command | `python -m rook_agent` |
| Environment prefix | `ROOK_` |
| Project configuration | `rook.toml` |
| Global configuration | `~/.config/rook/config.toml` |
| Project data | `.rook` |
| Benchmark data | `.rook-<benchmark>` |
| Repository directory | `Rook` |

## Migration policy

This is a clean break. Rook will not ship compatibility imports, command
aliases, environment-variable fallbacks, configuration fallbacks, or session
directory migration. Tests, benchmarks, documentation, examples, website
assets, package metadata, code identifiers, and user-facing copy all adopt the
canonical identifiers in one change.

## Verification contract

The repository must satisfy all of the following:

1. Package metadata exposes `rook-agent` and only the `rook` console command.
2. `rook_agent` imports and `python -m rook_agent` work from a clean install.
3. Configuration resolves only `ROOK_*`, `rook.toml`, `.config/rook`, and
   `.rook` paths.
4. Python tests and benchmark adapter tests import only `rook_agent`.
5. Text files and filenames contain no legacy brand identifier in any casing.
6. Documentation and website assets use the Rook filenames and copy.
7. The final repository directory is named `Rook`.

## Risk controls

The working directory has no Git metadata. The original source archive remains
outside the project directory, so the implementation uses a pre-change test
baseline, a contract test, file-count checks, import checks, packaging checks,
and a final repository-wide residue scan instead of commit-based rollback.
