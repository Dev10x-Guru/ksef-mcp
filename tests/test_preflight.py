from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from ksef_mcp import preflight

# Captured before the suite-wide fixture stands in for the machine: this one
# file is where the probe itself is under test, not something it answers for.
REAL_LOAD_SECRET_SERVICE = preflight.load_secret_service


def backend(module: str, priority: float) -> object:
    stub = type("Backend", (), {"__module__": module})()
    stub.priority = priority
    return stub


def raiser(error: Exception) -> Callable[..., object]:
    def raise_it(*args: object, **kwargs: object) -> object:
        raise error

    return raise_it


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
    monkeypatch.setattr(preflight, "load_secret_service", lambda: double)
    return double


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / preflight.PROJECT_MARKER).mkdir()
    return tmp_path


@pytest.fixture
def pinned_repository(repository: Path) -> Path:
    (repository / preflight.NODE_VERSION_FILE).write_text("22.17.0\n", encoding="utf-8")
    return repository


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v22.17.0", (22, 17, 0)),
        ("22.17.0\n", (22, 17, 0)),
        ("  v22.14.0  ", (22, 14, 0)),
        ("22.17", None),
        ("v22.17.x", None),
        ("lts/*", None),
    ],
)
def test_parse_version(raw: str, expected: tuple[int, int, int] | None) -> None:
    assert preflight.parse_version(raw) == expected


def test_pin_is_absent_without_the_file(repository: Path) -> None:
    assert preflight.read_pinned_node_version(working_directory=repository) is None


def test_pin_is_absent_when_the_file_is_unparsable(repository: Path) -> None:
    (repository / preflight.NODE_VERSION_FILE).write_text("lts/*\n", encoding="utf-8")

    assert preflight.read_pinned_node_version(working_directory=repository) is None


def test_pin_is_read_from_the_file(pinned_repository: Path) -> None:
    assert preflight.read_pinned_node_version(working_directory=pinned_repository) == (
        pinned_repository / preflight.NODE_VERSION_FILE,
        (22, 17, 0),
    )


def test_node_version_comes_from_the_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v22.17.0\n"),
    )

    assert preflight.query_node_version(executable="/usr/bin/node") == (22, 17, 0)


def test_node_version_is_unknown_when_the_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=1, stdout=""),
    )

    assert preflight.query_node_version(executable="/usr/bin/node") is None


@pytest.mark.parametrize(
    "error",
    [OSError("brak pliku"), TimeoutExpired(cmd="node", timeout=10.0)],
)
def test_node_version_is_unknown_when_the_call_raises(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(preflight.subprocess, "run", raiser(error))

    assert preflight.query_node_version(executable="/usr/bin/node") is None


def test_node_is_reported_missing_when_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    report = preflight.inspect_node(working_directory=repository)

    assert (report.executable, report.version, report.satisfies_requirement) == (
        None,
        None,
        False,
    )


def test_node_falls_back_to_the_generator_minimum_without_a_pin(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    report = preflight.inspect_node(working_directory=repository)

    assert (report.required, report.pinned_by) == (preflight.MINIMUM_NODE_VERSION, None)


def test_node_requirement_comes_from_the_pin_when_present(
    monkeypatch: pytest.MonkeyPatch,
    pinned_repository: Path,
) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v22.17.0"),
    )

    report = preflight.inspect_node(working_directory=pinned_repository)

    assert (report.required, report.satisfies_requirement) == ((22, 17, 0), True)


def test_a_pin_may_raise_the_floor_but_never_lower_it(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    # A stranger's project pinned to an older Node must not make the MF
    # generator's own minimum disappear.
    (repository / preflight.NODE_VERSION_FILE).write_text("20.11.0\n", encoding="utf-8")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v20.11.0"),
    )

    report = preflight.inspect_node(working_directory=repository)

    assert (report.required, report.satisfies_requirement) == (
        preflight.MINIMUM_NODE_VERSION,
        False,
    )


def test_a_pin_below_the_generator_minimum_is_flagged(repository: Path) -> None:
    (repository / preflight.NODE_VERSION_FILE).write_text("20.11.0\n", encoding="utf-8")

    report = preflight.inspect_node(working_directory=repository)

    assert report.pin_is_below_the_generator is True


def test_a_pin_without_a_project_marker_is_ignored(tmp_path: Path) -> None:
    # Someone else's global ~/.node-version is their preference for their own
    # work, not a statement about this project.
    (tmp_path / preflight.NODE_VERSION_FILE).write_text("20.11.0\n", encoding="utf-8")

    assert preflight.read_pinned_node_version(working_directory=tmp_path) is None


def test_the_walk_stops_at_the_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/")))

    assert preflight.read_pinned_node_version(working_directory=Path("/")) is None


def test_a_pin_is_found_from_a_subdirectory(pinned_repository: Path) -> None:
    nested = pinned_repository / "src" / "gdzieś" / "głęboko"
    nested.mkdir(parents=True)

    found = preflight.read_pinned_node_version(working_directory=nested)

    assert found == (pinned_repository / preflight.NODE_VERSION_FILE, (22, 17, 0))


def test_node_below_the_requirement_does_not_satisfy_it(
    monkeypatch: pytest.MonkeyPatch,
    pinned_repository: Path,
) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v20.11.0"),
    )

    report = preflight.inspect_node(working_directory=pinned_repository)

    assert (report.version, report.satisfies_requirement) == ((20, 11, 0), False)


def test_keyring_reports_no_store_when_only_sentinels_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preflight.keyring.backend,
        "get_all_keyring",
        lambda: [
            backend("keyring.backends.fail", 0),
            backend("keyring.backends.chainer", -1),
        ],
    )

    report = preflight.inspect_keyring()

    assert (report.backends, report.preferred, report.usable) == ((), None, False)


def test_a_locked_collection_is_reported_as_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    with_secret_service(monkeypatch, collection=FakeCollection(locked=True))

    assert preflight.inspect_collection_lock() is preflight.CollectionLock.LOCKED


def test_an_unlocked_collection_is_reported_as_unlocked(monkeypatch: pytest.MonkeyPatch) -> None:
    with_secret_service(monkeypatch, collection=FakeCollection(locked=False))

    assert preflight.inspect_collection_lock() is preflight.CollectionLock.UNLOCKED


def test_the_lock_state_is_read_without_ever_asking_to_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole point of bypassing the keyring API: `unlock()` opens a prompt
    # that hangs the stdio transport, so the probe must never reach it.
    collection = FakeCollection(locked=True)
    with_secret_service(monkeypatch, collection=collection)

    preflight.inspect_collection_lock()

    assert collection.unlock_calls == 0


def test_a_machine_without_d_bus_has_no_lock_state(monkeypatch: pytest.MonkeyPatch) -> None:
    with_secret_service(monkeypatch, collection=None)

    assert preflight.inspect_collection_lock() is preflight.CollectionLock.ABSENT


def test_a_platform_without_secret_service_has_no_lock_state() -> None:
    # No stubbing needed: the suite-wide fixture already answers "not here".
    assert preflight.inspect_collection_lock() is preflight.CollectionLock.ABSENT


def test_the_module_is_loaded_by_name_when_it_is_installed() -> None:
    # Linux-only by dependency marker, and the suite runs on Linux; the absence
    # path is the test above it.
    assert REAL_LOAD_SECRET_SERVICE() is not None


def test_an_uninstalled_secret_service_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight.importlib, "import_module", raiser(ImportError("brak modułu")))

    assert REAL_LOAD_SECRET_SERVICE() is None


def test_keyring_prefers_the_highest_priority_selectable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preflight.keyring.backend,
        "get_all_keyring",
        lambda: [
            backend("keyring.backends.chainer", -1),
            backend("keyring.backends.SecretService", 5),
            backend("keyring.backends.kwallet", 4.9),
        ],
    )

    report = preflight.inspect_keyring()

    assert (report.preferred, report.usable, len(report.backends)) == (
        "keyring.backends.SecretService",
        True,
        2,
    )
