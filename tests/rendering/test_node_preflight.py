from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from ksef_mcp.rendering import node_preflight
from tests.conftest import raiser


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / node_preflight.PROJECT_MARKER).mkdir()
    return tmp_path


@pytest.fixture
def pinned_repository(repository: Path) -> Path:
    (repository / node_preflight.NODE_VERSION_FILE).write_text("22.17.0\n", encoding="utf-8")
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
    assert node_preflight.parse_version(raw) == expected


def test_pin_is_absent_without_the_file(repository: Path) -> None:
    assert node_preflight.read_pinned_node_version(working_directory=repository) is None


def test_pin_is_absent_when_the_file_is_unparsable(repository: Path) -> None:
    (repository / node_preflight.NODE_VERSION_FILE).write_text("lts/*\n", encoding="utf-8")

    assert node_preflight.read_pinned_node_version(working_directory=repository) is None


def test_pin_is_read_from_the_file(pinned_repository: Path) -> None:
    assert node_preflight.read_pinned_node_version(working_directory=pinned_repository) == (
        pinned_repository / node_preflight.NODE_VERSION_FILE,
        (22, 17, 0),
    )


def test_node_version_comes_from_the_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v22.17.0\n"),
    )

    assert node_preflight.query_node_version(executable="/usr/bin/node") == (22, 17, 0)


def test_node_version_is_unknown_when_the_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=1, stdout=""),
    )

    assert node_preflight.query_node_version(executable="/usr/bin/node") is None


@pytest.mark.parametrize(
    "error",
    [OSError("brak pliku"), TimeoutExpired(cmd="node", timeout=10.0)],
)
def test_node_version_is_unknown_when_the_call_raises(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(node_preflight.subprocess, "run", raiser(error))

    assert node_preflight.query_node_version(executable="/usr/bin/node") is None


def test_node_is_reported_missing_when_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    monkeypatch.setattr(node_preflight.shutil, "which", lambda name: None)

    report = node_preflight.inspect_node(working_directory=repository)

    assert (report.executable, report.version, report.satisfies_requirement) == (
        None,
        None,
        False,
    )


def test_node_falls_back_to_the_generator_minimum_without_a_pin(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    monkeypatch.setattr(node_preflight.shutil, "which", lambda name: None)

    report = node_preflight.inspect_node(working_directory=repository)

    assert (report.required, report.pinned_by) == (node_preflight.MINIMUM_NODE_VERSION, None)


def test_node_requirement_comes_from_the_pin_when_present(
    monkeypatch: pytest.MonkeyPatch,
    pinned_repository: Path,
) -> None:
    monkeypatch.setattr(node_preflight.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        node_preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v22.17.0"),
    )

    report = node_preflight.inspect_node(working_directory=pinned_repository)

    assert (report.required, report.satisfies_requirement) == ((22, 17, 0), True)


def test_a_pin_may_raise_the_floor_but_never_lower_it(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    # A stranger's project pinned to an older Node must not make the MF
    # generator's own minimum disappear.
    (repository / node_preflight.NODE_VERSION_FILE).write_text("20.11.0\n", encoding="utf-8")
    monkeypatch.setattr(node_preflight.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        node_preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v20.11.0"),
    )

    report = node_preflight.inspect_node(working_directory=repository)

    assert (report.required, report.satisfies_requirement) == (
        node_preflight.MINIMUM_NODE_VERSION,
        False,
    )


def test_a_pin_below_the_generator_minimum_is_flagged(repository: Path) -> None:
    (repository / node_preflight.NODE_VERSION_FILE).write_text("20.11.0\n", encoding="utf-8")

    report = node_preflight.inspect_node(working_directory=repository)

    assert report.pin_is_below_the_generator is True


def test_a_pin_without_a_project_marker_is_ignored(tmp_path: Path) -> None:
    # Someone else's global ~/.node-version is their preference for their own
    # work, not a statement about this project.
    (tmp_path / node_preflight.NODE_VERSION_FILE).write_text("20.11.0\n", encoding="utf-8")

    assert node_preflight.read_pinned_node_version(working_directory=tmp_path) is None


def test_the_walk_stops_at_the_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/")))

    assert node_preflight.read_pinned_node_version(working_directory=Path("/")) is None


def test_a_pin_is_found_from_a_subdirectory(pinned_repository: Path) -> None:
    nested = pinned_repository / "src" / "gdzieś" / "głęboko"
    nested.mkdir(parents=True)

    found = node_preflight.read_pinned_node_version(working_directory=nested)

    assert found == (pinned_repository / node_preflight.NODE_VERSION_FILE, (22, 17, 0))


def test_node_below_the_requirement_does_not_satisfy_it(
    monkeypatch: pytest.MonkeyPatch,
    pinned_repository: Path,
) -> None:
    monkeypatch.setattr(node_preflight.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        node_preflight.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stdout="v20.11.0"),
    )

    report = node_preflight.inspect_node(working_directory=pinned_repository)

    assert (report.version, report.satisfies_requirement) == ((20, 11, 0), False)
