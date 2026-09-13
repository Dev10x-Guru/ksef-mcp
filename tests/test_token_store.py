from collections.abc import Callable

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from ksef_mcp import preflight, token_store
from ksef_mcp.metadata import SERVER_NAME

NIP = "1234567890"

TOKEN = "aaaabbbbccccdddd"


class FakeKeyring:
    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append((service, username))
        return self.stored.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.stored[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.stored:
            raise PasswordDeleteError(username)
        del self.stored[(service, username)]


class BrokenKeyring(FakeKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringError("brak backendu")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringError("brak backendu")

    def delete_password(self, service: str, username: str) -> None:
        raise KeyringError("brak backendu")


class LyingKeyring(FakeKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        return "coś zupełnie innego"


@pytest.fixture(autouse=True)
def without_exported_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real export in the developer's shell would otherwise decide the result
    # of every read in this file.
    monkeypatch.delenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, raising=False)


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    double = FakeKeyring()
    monkeypatch.setattr(token_store, "keyring", double)
    return double


@pytest.fixture
def broken_keyring(monkeypatch: pytest.MonkeyPatch) -> BrokenKeyring:
    double = BrokenKeyring()
    monkeypatch.setattr(token_store, "keyring", double)
    return double


@pytest.fixture
def lying_keyring(monkeypatch: pytest.MonkeyPatch) -> LyingKeyring:
    double = LyingKeyring()
    monkeypatch.setattr(token_store, "keyring", double)
    return double


@pytest.fixture
def locked_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preflight,
        "inspect_collection_lock",
        lambda: preflight.CollectionLock.LOCKED,
    )


@pytest.fixture
def stored_fingerprint(fake_keyring: FakeKeyring) -> token_store.TokenFingerprint:
    return token_store.store_token(nip=NIP, token=TOKEN)


def test_fingerprint_reveals_only_length_and_tail() -> None:
    described = token_store.fingerprint(TOKEN)

    assert (described.length, described.suffix) == (16, "dddd")


def test_stored_token_is_keyed_by_the_server_name(
    stored_fingerprint: token_store.TokenFingerprint,
    fake_keyring: FakeKeyring,
) -> None:
    assert fake_keyring.stored == {(SERVER_NAME, NIP): TOKEN}


def test_store_returns_the_fingerprint_of_what_it_wrote(
    stored_fingerprint: token_store.TokenFingerprint,
) -> None:
    assert (stored_fingerprint.length, stored_fingerprint.suffix) == (16, "dddd")


def test_store_reads_the_token_back_before_returning(
    stored_fingerprint: token_store.TokenFingerprint,
    fake_keyring: FakeKeyring,
) -> None:
    assert fake_keyring.calls == [(SERVER_NAME, NIP)]


def test_store_refuses_when_read_back_disagrees(lying_keyring: LyingKeyring) -> None:
    with pytest.raises(token_store.TokenVerificationFailed):
        token_store.store_token(nip=NIP, token=TOKEN)


def test_store_reports_an_unusable_keyring(broken_keyring: BrokenKeyring) -> None:
    with pytest.raises(token_store.TokenStoreUnavailable):
        token_store.store_token(nip=NIP, token=TOKEN)


def test_read_reports_an_unusable_keyring(broken_keyring: BrokenKeyring) -> None:
    with pytest.raises(token_store.TokenStoreUnavailable):
        token_store.read_token(nip=NIP)


TOUCHES: list[Callable[[], object]] = [
    lambda: token_store.read_token(nip=NIP),
    lambda: token_store.store_token(nip=NIP, token=TOKEN),
    lambda: token_store.delete_token(nip=NIP),
]

TOUCH_NAMES = ["read", "store", "delete"]


@pytest.mark.parametrize("touch", TOUCHES, ids=TOUCH_NAMES)
def test_a_locked_collection_stops_every_touch_of_the_secret(
    fake_keyring: FakeKeyring,
    locked_collection: None,
    touch: Callable[[], object],
) -> None:
    with pytest.raises(token_store.TokenStoreLocked):
        touch()


@pytest.mark.parametrize("touch", TOUCHES, ids=TOUCH_NAMES)
def test_a_locked_collection_leaves_the_keyring_untouched(
    fake_keyring: FakeKeyring,
    locked_collection: None,
    touch: Callable[[], object],
) -> None:
    # Reaching the keyring at all is the failure: its own code unlocks, and the
    # prompt that opens hangs the stdio transport (D-004, ST-3).
    with pytest.raises(token_store.TokenStoreLocked):
        touch()

    assert (fake_keyring.calls, fake_keyring.stored) == ([], {})


def test_the_locked_message_names_the_documented_fallback(locked_collection: None) -> None:
    with pytest.raises(token_store.TokenStoreLocked) as refusal:
        token_store.refuse_a_locked_collection()

    assert token_store.FALLBACK_ENVIRONMENT_VARIABLE in str(refusal.value)


def test_a_locked_collection_is_reported_as_an_unusable_store(locked_collection: None) -> None:
    # The CLI catches TokenStoreUnavailable at one place and turns it into an
    # exit code; a lock that escaped that hierarchy would surface as a crash.
    with pytest.raises(token_store.TokenStoreUnavailable):
        token_store.refuse_a_locked_collection()


def test_an_exported_token_is_read_without_consulting_the_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fallback exists for machines whose store cannot answer; making it
    # depend on that same store would close the escape hatch.
    monkeypatch.setenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, TOKEN)
    monkeypatch.setattr(
        preflight,
        "inspect_collection_lock",
        lambda: preflight.CollectionLock.LOCKED,
    )

    assert token_store.read_token(nip=NIP).value == TOKEN


def test_read_returns_none_for_an_unknown_nip(fake_keyring: FakeKeyring) -> None:
    assert token_store.read_token(nip=NIP) is None


def test_read_returns_the_stored_token(
    stored_fingerprint: token_store.TokenFingerprint,
) -> None:
    assert token_store.read_token(nip=NIP) == token_store.StoredToken(
        value=TOKEN,
        source=token_store.TokenSource.KEYRING,
    )


def test_the_exported_variable_is_the_documented_escape_path(
    fake_keyring: FakeKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, TOKEN)

    assert token_store.read_token(nip=NIP) == token_store.StoredToken(
        value=TOKEN,
        source=token_store.TokenSource.ENVIRONMENT,
    )


def test_the_exported_variable_works_without_any_keyring(
    broken_keyring: BrokenKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, TOKEN)

    assert token_store.read_token(nip=NIP).source is token_store.TokenSource.ENVIRONMENT


def test_an_empty_export_does_not_shadow_the_keyring(
    stored_fingerprint: token_store.TokenFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, "")

    assert token_store.read_token(nip=NIP).source is token_store.TokenSource.KEYRING


def test_an_export_cannot_mask_a_failed_write(
    lying_keyring: LyingKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, TOKEN)

    with pytest.raises(token_store.TokenVerificationFailed):
        token_store.store_token(nip=NIP, token=TOKEN)


def test_delete_removes_the_stored_token(
    stored_fingerprint: token_store.TokenFingerprint,
    fake_keyring: FakeKeyring,
) -> None:
    removal = token_store.delete_token(nip=NIP)

    assert (removal.removed_from_keyring, fake_keyring.stored) == (True, {})


def test_delete_reports_a_missing_token(fake_keyring: FakeKeyring) -> None:
    assert token_store.delete_token(nip=NIP).removed_from_keyring is False


def test_delete_admits_the_export_still_serves_the_token(
    stored_fingerprint: token_store.TokenFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(token_store.FALLBACK_ENVIRONMENT_VARIABLE, TOKEN)

    assert token_store.delete_token(nip=NIP).still_exported is True


def test_the_stored_token_never_appears_in_its_repr() -> None:
    stored = token_store.StoredToken(
        value=TOKEN,
        source=token_store.TokenSource.KEYRING,
    )

    assert TOKEN not in repr(stored)


def test_delete_reports_an_unusable_keyring(broken_keyring: BrokenKeyring) -> None:
    with pytest.raises(token_store.TokenStoreUnavailable):
        token_store.delete_token(nip=NIP)


def test_fallback_variable_is_the_documented_one() -> None:
    assert token_store.FALLBACK_ENVIRONMENT_VARIABLE == "KSEF_TOKEN"
