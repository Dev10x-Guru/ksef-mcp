"""Who may write into a subject's directory, and what makes a replacement durable.

Every store beside this module already wrote atomically — staging file, `fsync`,
`os.replace` inside one directory (D-006). What none of them had was
*exclusivity*: two writers each read the whole document, each changed its own
part, and the second one to finish wrote the first one's change out of
existence. Two MCP clients against one subject is an ordinary setup, and MCP
tools are synchronous, so the SDK runs them in a thread pool — the same race
happens inside a single process, with no second server involved (ADR-107).

Three primitives, and deliberately no more. `exclusive_write` serialises whole
load-modify-save cycles. `written_atomically` replaces the fixed `.tmp` name
that made two concurrent writers truncate each other's staging file before
either reached its rename. `replaced_durably` finishes the job the stores
started: the file's own bytes were flushed, the directory entry naming them was
not, so a power cut could leave a `rename(2)` that never happened.

Above them sits `json_written_atomically`, which is where a stored JSON document
gets its spelling. Every store had written that one expression itself, so the
durability invariant D-006 held only as long as seven copies agreed — and the
next store to be written would inherit whichever copy its author happened to
read (GH-145).

The lock refuses instead of waiting. A blocked MCP tool is a hung agent session,
and an unbounded wait against a lock a dead process left behind is worse than a
refusal that says which directory is busy.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from ksef_mcp.errors import KsefMcpError

LOCK_FILE: Final[str] = ".lock"

# The lock file sits in a directory that already holds AES keys and KSeF
# numbers, and it is created rather than found, so it is created closed.
LOCK_FILE_MODE: Final[int] = 0o600

STAGING_SUFFIX: Final[str] = ".tmp"

# Indented and un-escaped because a taxpayer opening one of these files to see
# what the tool remembers about them is a supported thing to do, and a Polish
# counterparty name spelled `ł` answers nothing. The trailing newline keeps
# the files usable from a shell.
DOCUMENT_INDENT: Final[int] = 2

# `flock` is held per open file description, so two threads of one process
# opening the lock file separately do contend — which is what makes the thread
# pool safe. It also means a thread re-entering its own lock would be refused by
# its own outer frame, so the depth is counted here and only the outermost frame
# touches the kernel. Per thread, never per process: a second thread must still
# be refused.
_reentry = threading.local()


class WriteExclusivityUnavailable(KsefMcpError):
    """Another writer holds this subject's directory, and waiting is not the answer."""


def require_schema(
    document: Mapping[str, object],
    *,
    expected: int,
    named: str,
    refused_as: type[KsefMcpError],
    consequence: str,
) -> None:
    """Refuse a document this build does not claim to understand.

    `.get` rather than indexing, so a document written before versioning existed
    — or one a crash truncated to the first few keys — is refused by name
    instead of raising a `KeyError` the reader cannot tell from corruption
    (GH-169). The consequence is spelled out per document because "refusing to
    guess" is only convincing when it says what the guess would cost.
    """
    found = document.get("schema_version")
    if found == expected:
        return
    raise refused_as(
        f"{named} is schema {found}, this build reads {expected}. Refusing to guess: {consequence}"
    )


def _depths() -> dict[Path, int]:
    depths: dict[Path, int] | None = getattr(_reentry, "depths", None)
    if depths is None:
        depths = {}
        _reentry.depths = depths
    return depths


@contextmanager
def exclusive_write(directory: Path, *, directory_mode: int) -> Iterator[None]:
    """Hold one subject's directory for the whole load-modify-save, or refuse it.

    The scope is the cycle, not the write. Locking only the `save` would still
    let two writers start from the same snapshot, and the lost update this
    exists to prevent happens between the read and the write, not during it.
    """
    directory.mkdir(mode=directory_mode, parents=True, exist_ok=True)
    resolved = directory.resolve()
    depths = _depths()
    if resolved in depths:
        depths[resolved] += 1
        try:
            yield
        finally:
            depths[resolved] -= 1
        return
    descriptor = os.open(directory / LOCK_FILE, os.O_WRONLY | os.O_CREAT, LOCK_FILE_MODE)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as taken:
            raise WriteExclusivityUnavailable(
                f"Another writer already holds {directory}. Refusing to queue "
                f"behind it: a blocked tool call is a hung agent session, and a "
                f"lock left by a killed process would never come free. Try again "
                f"once the other run has finished."
            ) from taken
        depths[resolved] = 1
        try:
            yield
        finally:
            del depths[resolved]
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def reserved_staging(target: Path, *, file_mode: int) -> Path:
    """An empty staging file beside the target, under a name no second writer picks.

    `mkstemp` rather than `<target>.tmp`, and `fchmod` on the descriptor rather
    than `chmod` on the path: the fixed name let two writers share one truncated
    file, and the path-based tightening left a window in which a document
    carrying AES keys or a counterparty's data existed under the process umask.
    """
    descriptor, name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=STAGING_SUFFIX,
    )
    try:
        os.fchmod(descriptor, file_mode)
    finally:
        os.close(descriptor)
    return Path(name)


def replaced_durably(staging: Path, target: Path) -> None:
    """Swap the staging file in, then persist the directory entry that names it.

    `os.replace` is atomic against readers; it is not durable against a power
    cut on its own. The bytes were already `fsync`-ed, so what a crash can still
    lose is the rename — the one step every store here relies on.
    """
    os.replace(staging, target)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def written_atomically(target: Path, *, content: bytes, file_mode: int) -> Path:
    """The whole document, written where nobody else can reach it, then swapped in."""
    staging = reserved_staging(target, file_mode=file_mode)
    try:
        descriptor = os.open(staging, os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # A unique staging name means a failed write no longer overwrites the
        # last one's leftovers, so nothing else will ever clean this up.
        staging.unlink(missing_ok=True)
        raise
    replaced_durably(staging, target)
    return target


def json_written_atomically(
    target: Path,
    *,
    document: Mapping[str, object],
    file_mode: int,
) -> Path:
    """One stored JSON document, in the one spelling every store here writes.

    Named rather than repeated because the serialisation and the durability are
    one promise: a store that indents differently is merely inconsistent, but a
    store that reaches past this to `written_atomically` with bytes of its own is
    one `fsync` away from breaking D-006 on its own (GH-145).
    """
    content = json.dumps(document, indent=DOCUMENT_INDENT, ensure_ascii=False) + "\n"
    return written_atomically(target, content=content.encode("utf-8"), file_mode=file_mode)


class SchemaMismatch(StrEnum):
    """What a store does when the document on disk is not the schema it reads.

    Two answers, and the difference between them is deliberate rather than
    historical. `REFUSE` is for anything a taxpayer's position depends on: a
    continuation point, a review ledger, a deduplication index. Guessing at one
    of those skips invoices or repeats them, and neither failure announces
    itself. `MISS` is for roots that are reconstructible by construction — the
    period cache, the allowance counters — where refusing to answer because a
    document is a version old would turn a saving into an outage.

    Named here so the next store picks its policy on purpose instead of
    inheriting whichever one its author happened to copy (GH-146).
    """

    REFUSE = "refuse"
    MISS = "miss"


@dataclass(frozen=True, kw_only=True)
class JsonDocumentStore:
    """One store's reading policy: which schema it claims, and what a mismatch means.

    Deliberately holds no path. The stores above pick their file from the
    subject and the environment, and a policy that also knew the path would have
    to be rebuilt for every read — this way it is a module-level constant that
    says, in one place, what the file is and how wrong it is allowed to be.

    `refused_as` and `consequence` speak only under `REFUSE`; a `MISS` store
    raises nothing and so names nothing.
    """

    schema_version: int
    file_mode: int
    named: str
    on_mismatch: SchemaMismatch = SchemaMismatch.REFUSE
    refused_as: type[KsefMcpError] = KsefMcpError
    consequence: str = ""

    def load(self, path: Path) -> dict[str, object] | None:
        """The stored document, or `None` when there is none this build can read.

        An absent file and — under `MISS` — an unreadable version arrive as the
        same `None`, because every caller answers them the same way: with the
        empty aggregate it would have started from anyway.
        """
        if not path.is_file():
            return None
        document: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        if self.on_mismatch is SchemaMismatch.MISS:
            if document.get("schema_version") != self.schema_version:
                return None
            return document
        require_schema(
            document,
            expected=self.schema_version,
            named=self.named,
            refused_as=self.refused_as,
            consequence=self.consequence,
        )
        return document

    def save(self, path: Path, *, document: Mapping[str, object]) -> Path:
        return json_written_atomically(path, document=document, file_mode=self.file_mode)
