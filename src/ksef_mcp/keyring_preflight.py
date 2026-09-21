"""Whether a secret store is reachable, and whether its collection is locked.

Separate from `rendering.node_preflight` for the reason given there: the
renderer has no business importing `keyring.backend` and the D-Bus probe, and
it did only because both checks happened to share a file (GH-125).
"""

import importlib
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType
from typing import Final

import keyring.backend

# `fail` is the sentinel keyring falls back to when no OS store is reachable,
# and `chainer` only delegates to the others. Neither is something a user can
# deliberately pick, so neither belongs on the choice list (D-004).
UNSELECTABLE_BACKEND_MODULES: Final[frozenset[str]] = frozenset(
    {"keyring.backends.fail", "keyring.backends.chainer"}
)

SECRET_SERVICE_MODULE: Final[str] = "secretstorage"


class CollectionLock(StrEnum):
    UNLOCKED = "unlocked"
    LOCKED = "locked"
    # No Secret Service to ask: macOS, Windows, or Linux without D-Bus. The
    # missing-backend case is ST-3's first failure mode and `inspect_keyring`
    # already answers it; here it only means this probe has nothing to say.
    ABSENT = "absent"


@dataclass(frozen=True)
class KeyringBackendReport:
    module: str
    priority: float


@dataclass(frozen=True)
class KeyringReport:
    backends: tuple[KeyringBackendReport, ...]
    preferred: str | None

    @property
    def usable(self) -> bool:
        return self.preferred is not None


def load_secret_service() -> ModuleType | None:
    # Imported by name rather than at module scope: `secretstorage` is a
    # dependency only where D-Bus exists, and this package also runs on macOS
    # and Windows. One function to stand in for the machine in tests, too.
    try:
        return importlib.import_module(SECRET_SERVICE_MODULE)
    except ImportError:
        return None


def inspect_collection_lock() -> CollectionLock:
    """Read whether the secret collection is locked, without asking to unlock it.

    `keyring.get_password()` on a locked collection calls `unlock()` itself, so
    the prompt opens inside a call that reads like a read — and on stdio that
    hangs the MCP transport (D-004, ST-3). Reading the D-Bus property directly
    is the only way to learn this for free, so it bypasses the keyring API.
    """
    secret_service = load_secret_service()
    if secret_service is None:
        return CollectionLock.ABSENT
    try:
        collection = secret_service.get_default_collection(secret_service.dbus_init())
    except secret_service.SecretStorageException:
        return CollectionLock.ABSENT
    return CollectionLock.LOCKED if collection.is_locked() else CollectionLock.UNLOCKED


def inspect_keyring() -> KeyringReport:
    # Enumeration alone: no read, no write, no prompt. That is what makes this
    # safe to call from a process that also speaks the stdio transport, where
    # an interactive password prompt would hang the protocol (D-004).
    discovered = tuple(
        KeyringBackendReport(
            module=type(backend).__module__,
            priority=float(backend.priority),
        )
        for backend in keyring.backend.get_all_keyring()
    )
    selectable = tuple(
        report for report in discovered if report.module not in UNSELECTABLE_BACKEND_MODULES
    )
    preferred = max(selectable, key=lambda report: report.priority).module if selectable else None
    return KeyringReport(backends=selectable, preferred=preferred)
