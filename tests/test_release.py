"""The version arithmetic behind an irreversible publication.

Only the pure parts are exercised here: everything that talks to git, gh or
PyPI belongs to a release nobody should be able to trigger from a test.
"""

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

MODULE_NAME = "release_script"


@pytest.fixture(scope="module")
def release() -> Iterator[ModuleType]:
    # Not importable as a package: `bin/` is a script directory with a uv
    # shebang and PEP 723 metadata, deliberately outside `src/`. The entry in
    # `sys.modules` is removed afterwards so the rest of the run does not
    # inherit a module nothing imported.
    path = Path(__file__).resolve().parent.parent / "bin" / "release.py"
    specification = importlib.util.spec_from_file_location(MODULE_NAME, path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[MODULE_NAME] = module
    specification.loader.exec_module(module)
    yield module
    del sys.modules[MODULE_NAME]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.2.0", (0, 2, 0, False)),
        ("0.2.1.dev0", (0, 2, 1, True)),
        ("10.0.3", (10, 0, 3, False)),
    ],
)
def test_a_version_is_read_with_its_development_marker(
    release: ModuleType, text: str, expected: tuple[int, int, int, bool]
) -> None:
    parsed = release.parse_version(text)

    assert (parsed.major, parsed.minor, parsed.patch, parsed.development) == expected


@pytest.mark.parametrize("text", ["0.2", "0.2.0.post1", "v0.2.0", "0.2.0dev0", ""])
def test_a_number_we_cannot_read_stops_the_release(release: ModuleType, text: str) -> None:
    # Guessing here would publish a number nobody chose.
    with pytest.raises(release.ReleaseRefused):
        release.parse_version(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.2.0", "0.2.0"), ("0.2.1.dev0", "0.2.1.dev0")],
)
def test_a_version_round_trips_through_its_text(
    release: ModuleType, text: str, expected: str
) -> None:
    assert str(release.parse_version(text)) == expected


def test_the_released_number_never_carries_the_suffix(release: ModuleType) -> None:
    assert release.parse_version("0.2.1.dev0").released == "0.2.1"


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        # A `.dev0` already claims the next patch, so fixes drops the suffix
        # rather than counting higher — otherwise every release skips a number.
        ("0.2.1.dev0", "patch", "0.2.1"),
        ("0.2.1.dev0", "minor", "0.3.0"),
        ("0.2.1.dev0", "major", "1.0.0"),
        # The transitional state: a tree still on a clean number, from before
        # the suffix was introduced.
        ("0.2.0", "patch", "0.2.1"),
        ("0.2.0", "minor", "0.3.0"),
        ("0.2.0", "major", "1.0.0"),
    ],
)
def test_the_release_number_follows_from_the_tree_and_the_kind(
    release: ModuleType, current: str, kind: str, expected: str
) -> None:
    assert release.version_to_release(release.parse_version(current), kind=kind) == expected


@pytest.mark.parametrize(
    ("released", "expected"),
    [("0.2.1", "0.2.2.dev0"), ("1.0.0", "1.0.1.dev0"), ("0.3.0", "0.3.1.dev0")],
)
def test_work_reopens_on_the_next_patch(release: ModuleType, released: str, expected: str) -> None:
    assert release.next_development_version(released) == expected


@pytest.fixture
def outstanding(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, bool, bool], str | None]:
    """The reconciliation decision, without git or gh in the way."""

    def decide(version: str, *, tagged: bool, released: bool) -> str | None:
        monkeypatch.setattr(
            release, "remote_tag_commit", lambda project, *, tag: "abc123" if tagged else None
        )
        monkeypatch.setattr(release, "github_release_exists", lambda project, *, tag: released)
        project = release.Project(root=Path("/nonexistent"), name="ksef-mcp", version=version)
        return release.outstanding_development_bump(project)

    return decide


def test_a_tree_already_reopened_needs_no_reconciliation(
    outstanding: Callable[..., str | None],
) -> None:
    assert outstanding("0.2.2.dev0", tagged=True, released=True) is None


def test_a_number_that_never_reached_the_remote_is_not_reconciled(
    outstanding: Callable[..., str | None],
) -> None:
    assert outstanding("0.2.1", tagged=False, released=False) is None


def test_a_half_finished_release_is_left_to_the_resume_path(
    outstanding: Callable[..., str | None],
) -> None:
    # Tag pushed but no GitHub release: that is a release to finish, not a
    # reopening to reconcile.
    assert outstanding("0.2.1", tagged=True, released=False) is None


def test_a_finished_release_on_a_clean_number_reopens_the_next_one(
    outstanding: Callable[..., str | None],
) -> None:
    # The state that used to start a brand-new release and silently skip the
    # reopening for good — every later wheel then matched the published one.
    assert outstanding("0.2.1", tagged=True, released=True) == "0.2.2.dev0"


def test_releasing_twice_in_a_row_never_reuses_a_number(release: ModuleType) -> None:
    # The invariant the suffix exists to protect: whatever a release produces,
    # the tree reopens above it, so the next release cannot land on it again.
    released = release.version_to_release(release.parse_version("0.2.1.dev0"), kind="patch")
    reopened = release.next_development_version(released)

    following = release.version_to_release(release.parse_version(reopened), kind="patch")

    assert following != released
