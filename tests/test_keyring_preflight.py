import pytest

from ksef_mcp import keyring_preflight
from tests.conftest import raiser

# Captured before the suite-wide fixture stands in for the machine: this one
# file is where the probe itself is under test, not something it answers for.
REAL_LOAD_SECRET_SERVICE = keyring_preflight.load_secret_service


def backend(module: str, priority: float) -> object:
    stub = type("Backend", (), {"__module__": module})()
    stub.priority = priority
    return stub


class FakeSecretStorageException(Exception):
    pass


class FakeCollection:
    def __init__(self, *, locked: bool) -> None:
        self.locked = locked
        self.unlock_calls = 0

    def is_locked(self) -> bool:
        return self.locked

    def unlock(self) -> None:
        self.unlock_calls += 1


class FakeSecretService:
    """Stands in for `secretstorage`: a connection, a collection, one exception."""

    SecretStorageException = FakeSecretStorageException

    def __init__(self, *, collection: FakeCollection | None) -> None:
        self.collection = collection

    def dbus_init(self) -> object:
        if self.collection is None:
            raise FakeSecretStorageException("brak magistrali")
        return object()

    def get_default_collection(self, connection: object) -> FakeCollection:
        assert self.collection is not None
        return self.collection


def with_secret_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    collection: FakeCollection | None,
) -> FakeSecretService:
    double = FakeSecretService(collection=collection)
    monkeypatch.setattr(keyring_preflight, "load_secret_service", lambda: double)
    return double


def test_keyring_reports_no_store_when_only_sentinels_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        keyring_preflight.keyring.backend,
        "get_all_keyring",
        lambda: [
            backend("keyring.backends.fail", 0),
            backend("keyring.backends.chainer", -1),
        ],
    )

    report = keyring_preflight.inspect_keyring()

    assert (report.backends, report.preferred, report.usable) == ((), None, False)


def test_a_locked_collection_is_reported_as_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    with_secret_service(monkeypatch, collection=FakeCollection(locked=True))

    assert keyring_preflight.inspect_collection_lock() is keyring_preflight.CollectionLock.LOCKED


def test_an_unlocked_collection_is_reported_as_unlocked(monkeypatch: pytest.MonkeyPatch) -> None:
    with_secret_service(monkeypatch, collection=FakeCollection(locked=False))

    assert keyring_preflight.inspect_collection_lock() is keyring_preflight.CollectionLock.UNLOCKED


def test_the_lock_state_is_read_without_ever_asking_to_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole point of bypassing the keyring API: `unlock()` opens a prompt
    # that hangs the stdio transport, so the probe must never reach it.
    collection = FakeCollection(locked=True)
    with_secret_service(monkeypatch, collection=collection)

    keyring_preflight.inspect_collection_lock()

    assert collection.unlock_calls == 0


def test_a_machine_without_d_bus_has_no_lock_state(monkeypatch: pytest.MonkeyPatch) -> None:
    with_secret_service(monkeypatch, collection=None)

    assert keyring_preflight.inspect_collection_lock() is keyring_preflight.CollectionLock.ABSENT


def test_a_platform_without_secret_service_has_no_lock_state() -> None:
    # No stubbing needed: the suite-wide fixture already answers "not here".
    assert keyring_preflight.inspect_collection_lock() is keyring_preflight.CollectionLock.ABSENT


def test_the_module_is_loaded_by_name_when_it_is_installed() -> None:
    # Linux-only by dependency marker, and the suite runs on Linux; the absence
    # path is the test above it.
    assert REAL_LOAD_SECRET_SERVICE() is not None


def test_an_uninstalled_secret_service_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        keyring_preflight.importlib,
        "import_module",
        raiser(ImportError("brak modułu")),
    )

    assert REAL_LOAD_SECRET_SERVICE() is None


def test_keyring_prefers_the_highest_priority_selectable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        keyring_preflight.keyring.backend,
        "get_all_keyring",
        lambda: [
            backend("keyring.backends.chainer", -1),
            backend("keyring.backends.SecretService", 5),
            backend("keyring.backends.kwallet", 4.9),
        ],
    )

    report = keyring_preflight.inspect_keyring()

    assert (report.preferred, report.usable, len(report.backends)) == (
        "keyring.backends.SecretService",
        True,
        2,
    )
