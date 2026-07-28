"""System credential storage for Provider API keys."""

from __future__ import annotations


SERVICE_NAME = "rook-agent"


class CredentialStoreError(RuntimeError):
    """Raised when a credential cannot be persisted safely."""


def read_api_key(name: str) -> str | None:
    """Read one API key from the operating-system credential backend.

    Missing or unavailable credential backends behave like a missing key so
    non-interactive commands can return the normal setup guidance.
    """

    try:
        import keyring
    except ImportError:
        return None
    try:
        value = keyring.get_password(SERVICE_NAME, name)
    except Exception:
        return None
    return value or None


def write_api_key(name: str, value: str) -> None:
    """Persist one non-empty API key without writing it to Rook TOML files."""

    if not name or not value:
        raise CredentialStoreError("credential name and value must not be empty")
    try:
        import keyring
    except ImportError as exc:
        raise CredentialStoreError(
            "system credential storage is unavailable; install the 'keyring' dependency"
        ) from exc
    try:
        keyring.set_password(SERVICE_NAME, name, value)
    except Exception as exc:
        raise CredentialStoreError(
            "the operating-system credential manager rejected the API key"
        ) from exc
