"""What one writer is guaranteed while another is running.

The races these tests are about are real ones, so the tests make them real: a
second thread that genuinely opens the lock file for itself, with barriers
instead of sleeps. A mocked `flock` would assert that the code calls the
function the fix names, and that is precisely not the question — the question is
whether a second writer is actually stopped.
"""

import os
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ksef_mcp.storage import (
    LOCK_FILE,
    STAGING_SUFFIX,
    WriteExclusivityUnavailable,
    exclusive_write,
    replaced_durably,
    reserved_staging,
    written_atomically,
)

DIRECTORY_MODE = 0o700

FILE_MODE = 0o600

# Long enough that a loaded machine never trips it, short enough that a genuine
# deadlock fails the suite instead of hanging it.
BARRIER_TIMEOUT = 5.0


@dataclass
class OtherWriter:
    """A second thread holding the directory until the test says to let go."""

    subject: Path
    taken: threading.Barrier = field(default_factory=lambda: threading.Barrier(2))
    released: threading.Barrier = field(default_factory=lambda: threading.Barrier(2))
    thread: threading.Thread | None = None

    def _hold(self) -> None:
        with exclusive_write(self.subject, directory_mode=DIRECTORY_MODE):
            self.taken.wait(timeout=BARRIER_TIMEOUT)
            self.released.wait(timeout=BARRIER_TIMEOUT)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._hold)
        self.thread.start()
        self.taken.wait(timeout=BARRIER_TIMEOUT)

    def finish(self) -> None:
        self.released.wait(timeout=BARRIER_TIMEOUT)
        assert self.thread is not None
        self.thread.join(timeout=BARRIER_TIMEOUT)
        assert not self.thread.is_alive()


def refused(subject: Path) -> bool:
    """Whether this caller was turned away from the directory."""
    try:
        with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
            return False
    except WriteExclusivityUnavailable:
        return True


def refuse_the_disk(descriptor: int) -> None:
    raise OSError("the disk answered no")


@pytest.fixture
def subject(tmp_path: Path) -> Path:
    return tmp_path / "subjects" / "1234567890" / "test"


@pytest.fixture
def target(subject: Path) -> Path:
    subject.mkdir(mode=DIRECTORY_MODE, parents=True)
    return subject / "document.json"


@pytest.fixture
def occupied(subject: Path) -> Iterator[OtherWriter]:
    writer = OtherWriter(subject=subject)
    writer.start()
    yield writer
    writer.finish()


def test_the_first_writer_gets_the_directory(subject: Path) -> None:
    with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
        assert (subject / LOCK_FILE).is_file()


def test_the_lock_file_is_readable_only_by_its_owner(subject: Path) -> None:
    with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
        pass

    assert stat.S_IMODE((subject / LOCK_FILE).stat().st_mode) == FILE_MODE


def test_a_second_thread_is_refused_while_the_first_holds_it(
    subject: Path,
    occupied: OtherWriter,
) -> None:
    assert refused(subject) is True


def test_the_refusal_names_the_directory_somebody_else_is_writing(
    subject: Path,
    occupied: OtherWriter,
) -> None:
    with pytest.raises(WriteExclusivityUnavailable, match=str(subject)):
        with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
            raise AssertionError


def test_the_directory_is_free_again_once_the_holder_returns(subject: Path) -> None:
    writer = OtherWriter(subject=subject)
    writer.start()
    writer.finish()

    assert refused(subject) is False


def test_one_thread_may_re_enter_its_own_hold(subject: Path) -> None:
    with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
        with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
            assert (subject / LOCK_FILE).is_file()


def test_leaving_an_inner_hold_does_not_free_the_directory(subject: Path) -> None:
    turned_away: list[bool] = []
    with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
        with exclusive_write(subject, directory_mode=DIRECTORY_MODE):
            pass
        thread = threading.Thread(target=lambda: turned_away.append(refused(subject)))
        thread.start()
        thread.join(timeout=BARRIER_TIMEOUT)

    assert turned_away == [True]


def test_the_written_document_holds_exactly_what_was_given(target: Path) -> None:
    written_atomically(target, content=b"{}\n", file_mode=FILE_MODE)

    assert target.read_bytes() == b"{}\n"


def test_the_written_document_is_readable_only_by_its_owner(target: Path) -> None:
    written_atomically(target, content=b"{}\n", file_mode=FILE_MODE)

    assert stat.S_IMODE(target.stat().st_mode) == FILE_MODE


def test_the_write_leaves_no_staging_file_behind(target: Path) -> None:
    written_atomically(target, content=b"{}\n", file_mode=FILE_MODE)

    assert list(target.parent.glob(f"*{STAGING_SUFFIX}")) == []


def test_two_writers_never_share_a_staging_name(target: Path) -> None:
    first = reserved_staging(target, file_mode=FILE_MODE)
    second = reserved_staging(target, file_mode=FILE_MODE)

    assert first != second


def test_a_reserved_staging_file_starts_at_its_final_mode(target: Path) -> None:
    staging = reserved_staging(target, file_mode=FILE_MODE)

    assert stat.S_IMODE(staging.stat().st_mode) == FILE_MODE


def test_a_failed_write_leaves_nothing_for_anyone_to_find(
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "fsync", refuse_the_disk)

    with pytest.raises(OSError, match="the disk answered no"):
        written_atomically(target, content=b"{}\n", file_mode=FILE_MODE)

    assert list(target.parent.glob(f"*{STAGING_SUFFIX}")) == []


def test_a_failed_write_leaves_the_previous_document_intact(
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written_atomically(target, content=b"first\n", file_mode=FILE_MODE)
    monkeypatch.setattr(os, "fsync", refuse_the_disk)

    with pytest.raises(OSError, match="the disk answered no"):
        written_atomically(target, content=b"second\n", file_mode=FILE_MODE)

    assert target.read_bytes() == b"first\n"


def test_the_replacement_puts_the_staging_file_in_place(target: Path) -> None:
    staging = reserved_staging(target, file_mode=FILE_MODE)
    staging.write_bytes(b"moved\n")

    replaced_durably(staging, target)

    assert target.read_bytes() == b"moved\n"


def test_the_replacement_takes_the_staging_name_out_of_the_directory(target: Path) -> None:
    staging = reserved_staging(target, file_mode=FILE_MODE)

    replaced_durably(staging, target)

    assert not staging.exists()
