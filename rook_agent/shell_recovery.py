"""Shared policy and classifiers for bounded Windows shell recovery."""

from __future__ import annotations


RESTRICTED_POWERSHELL_FAILURE_LIMIT = 2
SHELL_FALLBACK_EXHAUSTED_MARKER = "ROOK_SHELL_FALLBACK_EXHAUSTED"

_RESTRICTED_POWERSHELL_MARKERS = (
    "cannot dot-source this command because it was defined in a different language mode",
    "cannot create type. only core types are supported in this language mode",
    "method invocation is supported only on core types in this language mode",
    "powershell is in constrainedlanguage mode",
    "powershell is in constrained language mode",
)

WINDOWS_RESTRICTED_SHELL_GUIDANCE = (
    "- Treat language-mode, profile-loading, and method-invocation errors as "
    "restricted PowerShell failures.\n"
    f"- After {RESTRICTED_POWERSHELL_FAILURE_LIMIT} consecutive restricted "
    "PowerShell failures, do not try another PowerShell variant; switch once to "
    "cmd.exe /d /s /c, a direct executable such as py, or a dedicated non-shell "
    "tool when available.\n"
    "- Use the fallback to perform the task directly, not for capability probes "
    "or checks of outputs that were never created.\n"
    "- A direct py -c fallback must be one physical line with shell-safe "
    "statements. Do not pass multiline source or escaped newline sequences to "
    "py -c, and do not use a PowerShell here-string to feed it.\n"
    "- If the single fallback attempt fails, stop issuing shell commands and report "
    f"{SHELL_FALLBACK_EXHAUSTED_MARKER}: <short reason>.\n"
)


def is_restricted_powershell_failure(output: str) -> bool:
    """Return whether a failed command reports a restricted PowerShell boundary."""

    folded = output.casefold()
    return any(marker in folded for marker in _RESTRICTED_POWERSHELL_MARKERS)


def is_shell_fallback_exhausted_report(text: str | None) -> bool:
    """Recognize only an explicit final-report marker, not incidental mentions."""

    return (
        isinstance(text, str)
        and text.lstrip().startswith(f"{SHELL_FALLBACK_EXHAUSTED_MARKER}:")
    )


__all__ = [
    "RESTRICTED_POWERSHELL_FAILURE_LIMIT",
    "SHELL_FALLBACK_EXHAUSTED_MARKER",
    "WINDOWS_RESTRICTED_SHELL_GUIDANCE",
    "is_restricted_powershell_failure",
    "is_shell_fallback_exhausted_report",
]
