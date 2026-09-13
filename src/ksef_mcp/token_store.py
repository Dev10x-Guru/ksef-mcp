import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from ksef_mcp import preflight
from ksef_mcp.metadata import SERVER_NAME

SUFFIX_LENGTH: Final[int] = 4

FALLBACK_ENVIRONMENT_VARIABLE: Final[str] = "KSEF_TOKEN"

UNAVAILABLE_MESSAGE: Final[str] = (
    "System keyring is unavailable or refused the operation. "
    "Run `ksef-mcp onboarding` to choose a backend, or export "
    f"{FALLBACK_ENVIRONMENT_VARIABLE} as the documented fallback."
)


LOCKED_MESSAGE: Final[str] = (
    "The system keyring collection is locked, so nothing was read or written: "
    "asking the keyring would open an unlock prompt and hang the MCP "
    "transport. Unlock the collection in your desktop session, or export "
    f"{FALLBACK_ENVIRONMENT_VARIABLE} as the documented fallback."
)


class TokenStoreUnavailable(RuntimeError):
    pass


class TokenStoreLocked(TokenStoreUnavailable):
    """The store exists and would answer — after a prompt we must never open."""


def refuse_a_locked_collection() -> None:
    # Called before every single touch of the secret, not once at startup: a
    # collection locks itself again when the machine suspends or the store's
    # own timeout expires, and the second touch would be the one that hangs.
    if preflight.inspect_collection_lock() is preflight.CollectionLock.LOCKED:
        raise TokenStoreLocked(LOCKED_MESSAGE)


class TokenVerificationFailed(RuntimeError):
    pass


class TokenSource(StrEnum):
    ENVIRONMENT = "environment"
    KEYRING = "keyring"


@dataclass(frozen=True)
class TokenFingerprint:
    length: int
    suffix: str


@dataclass(frozen=True)
class StoredToken:
    # Kept out of the generated repr: an instance reaching a traceback with
    # locals, `pytest --showlocals` in a CI log, or an MCP error payload would
    # otherwise print the whole token. TokenFingerprint exists for showing it.
    value: str = field(repr=False)
    source: TokenSource


@dataclass(frozen=True)
class Removal:
    removed_from_keyring: bool
    still_exported: bool


def fingerprint(token: str) -> TokenFingerprint:
    return TokenFingerprint(length=len(token), suffix=token[-SUFFIX_LENGTH:])


def read_from_keyring(*, nip: str) -> str | None:
    # The service name is the MCP server identity, imported rather than spelled
    # out: a literal here would orphan every stored token the day SERVER_NAME
    # changes, and the symptom would be "the token vanished".
    refuse_a_locked_collection()
    try:
        return keyring.get_password(SERVER_NAME, nip)
    except KeyringError as error:
        raise TokenStoreUnavailable(UNAVAILABLE_MESSAGE) from error


def read_token(*, nip: str) -> StoredToken | None:
    # The environment variable wins when set. It is the escape path a person
    # picks deliberately when no keyring exists (D-004), and quietly preferring
    # the keyring over it would make the export look ignored.
    # It is deliberately NOT keyed by NIP — a shell has one of it — so with two
    # subjects configured both resolve to the same token. Callers that know
    # which subject they meant say so out loud.
    exported = os.environ.get(FALLBACK_ENVIRONMENT_VARIABLE)
    if exported:
        return StoredToken(value=exported, source=TokenSource.ENVIRONMENT)
    stored = read_from_keyring(nip=nip)
    if stored is None:
        return None
    return StoredToken(value=stored, source=TokenSource.KEYRING)


def store_token(*, nip: str, token: str) -> TokenFingerprint:
    refuse_a_locked_collection()
    try:
        keyring.set_password(SERVER_NAME, nip, token)
    except KeyringError as error:
        raise TokenStoreUnavailable(UNAVAILABLE_MESSAGE) from error
    # A KSeF token is displayed once and cannot be retrieved from the portal
    # again, so "the write returned" is not good enough. Read it back before
    # the user closes the only window that ever showed it — and read the
    # keyring itself, since an exported variable would mask a failed write.
    # No NIP in the message: an exception survives into tracebacks, MCP error
    # payloads and captured stderr, where the taxpayer identifier has no
    # business being. The caller printed it a line earlier anyway.
    if read_from_keyring(nip=nip) != token:
        raise TokenVerificationFailed(
            "The keyring accepted the token but returned something else on "
            "read-back. Do not discard the token yet."
        )
    return fingerprint(token)


def delete_token(*, nip: str) -> Removal:
    # Deleting the keyring entry is not the same as revoking access: an
    # exported variable outranks the keyring on read, so a bare "deleted"
    # would be false in the one direction that matters for a secret.
    still_exported = bool(os.environ.get(FALLBACK_ENVIRONMENT_VARIABLE))
    refuse_a_locked_collection()
    try:
        keyring.delete_password(SERVER_NAME, nip)
    except PasswordDeleteError:
        return Removal(removed_from_keyring=False, still_exported=still_exported)
    except KeyringError as error:
        raise TokenStoreUnavailable(UNAVAILABLE_MESSAGE) from error
    return Removal(removed_from_keyring=True, still_exported=still_exported)
