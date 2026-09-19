"""Where synchronisation state lives between runs, and how it survives a crash.

Continuation points and queued exports go to the platformdirs *data* directory,
never the cache one: the cache convention is "safe to delete at any moment",
and a disk cleaner honouring it would silently cost a full resynchronisation
against an allowance of twenty exports an hour (D-032).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Final

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.types import (
    ContinuationPoint,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    InvoiceDirection,
)
from ksef_mcp.paths import SubjectScope
from ksef_mcp.storage import exclusive_write, written_atomically

STATE_FILE: Final[str] = "synchronisation.json"

# Bumped whenever the document below stops being readable by the previous
# reader. A record written by a newer version is refused rather than guessed
# at: half-understood continuation points skip invoices, and nothing later
# asks for them again.
SCHEMA_VERSION: Final[int] = 1

STATE_DIRECTORY_MODE: Final[int] = 0o700

# How many finished exports the journal below keeps. The whole document is read
# and written on every pass, so an unbounded journal turns one refused export
# into a file that grows for as long as the subject is synchronised. Only the
# recent references are ever the ones somebody asks about.
SETTLED_JOURNAL_LIMIT: Final[int] = 50

# The file carries the AES key of every queued export (D-033), so it is created
# with its final mode rather than written and then tightened.
STATE_FILE_MODE: Final[int] = 0o600


class SyncStateUnreadable(KsefMcpError):
    """The record on disk was written by something this build does not understand."""


class ExportKeyDiscarded(KsefMcpError):
    """Asked for the key of an export that has already finished."""


@dataclass(frozen=True)
class PendingExport:
    """A package KSeF has been asked for and this process has not yet archived.

    Persisted the moment the export is queued, because the export is
    asynchronous: the MCP server under `uvx` is routinely killed together with
    the agent session between the request and the parts becoming available, and
    a key held only in memory would burn one of twenty hourly exports (D-033).

    `encryption` is empty once the export is over: a key outlives nothing it
    could still open, and a package KSeF refused will never be fetched (D-033).

    `covering_from` is where the subject type stood when this export was asked
    for. It is the only way back if the package turns out to be unreachable: the
    point moves on what KSeF confirmed rather than on what this process wrote
    (ADR-103 §3), so by the time a package is found dead the point is already
    past the window it covered (GH-93). A record written before that field
    existed carries `None`, which reads as "the window start was not kept" and
    not as "the export covered nothing".
    """

    reference: str
    direction: InvoiceDirection
    started_at: datetime
    encryption: ExportEncryption | None
    state: ExportState
    parts: tuple[ExportPart, ...] = ()
    invoice_count: int = 0
    covering_from: datetime | None = None

    @property
    def finished(self) -> bool:
        """Whether no later pass can carry this export any further.

        `READY` is not finished: the package exists and still has to be fetched
        and archived, and the record is what keeps its key. Only a refusal ends
        an export where it stands.
        """
        return self.state is ExportState.FAILED

    @property
    def handle(self) -> ExportHandle:
        if self.encryption is None:
            raise ExportKeyDiscarded(
                f"Export {self.reference} carries no key: it finished, and the "
                f"key stopped existing with it. Nothing can be fetched for it."
            )
        return ExportHandle(reference=self.reference, encryption=self.encryption)


@dataclass(frozen=True)
class DirectionState:
    """One subject type's place in the sequence, plus when it was last asked.

    `attempted_at` is scheduling, not domain: it enforces the fifteen-minute
    floor per subject type (D-031 §5) across process restarts, which an
    in-memory budget counter cannot do.
    """

    reached: datetime
    attempted_at: datetime | None = None

    def continuation_point(self, *, direction: InvoiceDirection) -> ContinuationPoint:
        return ContinuationPoint(direction=direction, reached=self.reached)


@dataclass(frozen=True)
class SyncState:
    """What a subject owes KSeF, and what it has stopped owing.

    Two tuples rather than one, because they answer different questions.
    `pending` is the work queue: everything a subject type is still waiting on,
    and the only thing a pass consults before deciding what to ask for next.
    `settled` is the journal: exports that ended and can never be continued.

    One tuple held both, and a refused export then sat at the head of the queue
    forever — `pending_for` reads the queue, so the subject type asked for a new
    package every fifteen minutes and collected none of them (GH-94). A dead
    letter belongs in a journal; leaving it in the work queue is what made the
    server look, to the Ministry, like a client working around its allowance.
    """

    directions: dict[InvoiceDirection, DirectionState] = field(default_factory=dict)
    pending: tuple[PendingExport, ...] = ()
    settled: tuple[PendingExport, ...] = ()

    def pending_for(self, direction: InvoiceDirection) -> PendingExport | None:
        return next((export for export in self.pending if export.direction == direction), None)

    def without_pending(self, *, reference: str) -> tuple[PendingExport, ...]:
        return tuple(export for export in self.pending if export.reference != reference)

    def with_pending(self, export: PendingExport) -> SyncState:
        return replace(self, pending=(*self.without_pending(reference=export.reference), export))

    def with_settled(self, export: PendingExport) -> SyncState:
        """Take a finished export off the queue and write it into the journal.

        Kept rather than dropped: a reference nobody can explain later is worse
        than one marked as the failure it was. Kept *here* rather than in
        `pending`, because a record that can never be continued would otherwise
        keep answering "what is this subject type waiting on" for good.
        """
        return replace(
            self,
            pending=self.without_pending(reference=export.reference),
            settled=(*self.settled, export)[-SETTLED_JOURNAL_LIMIT:],
        )

    def without_export(self, *, reference: str) -> SyncState:
        return replace(self, pending=self.without_pending(reference=reference))

    def with_direction(self, direction: InvoiceDirection, state: DirectionState) -> SyncState:
        return replace(self, directions={**self.directions, direction: state})


def _encode_part(part: ExportPart) -> dict[str, object]:
    return {
        "ordinal": part.ordinal,
        "name": part.name,
        "method": part.method,
        "url": part.url,
        "size_bytes": part.size_bytes,
        "content_hash": part.content_hash,
        "encrypted_size_bytes": part.encrypted_size_bytes,
        "encrypted_content_hash": part.encrypted_content_hash,
    }


def _decode_part(stored: dict[str, object]) -> ExportPart:
    return ExportPart(
        ordinal=int(stored["ordinal"]),  # type: ignore[arg-type]
        name=str(stored["name"]),
        method=str(stored["method"]),
        url=str(stored["url"]),
        size_bytes=int(stored["size_bytes"]),  # type: ignore[arg-type]
        content_hash=str(stored["content_hash"]),
        encrypted_size_bytes=int(stored["encrypted_size_bytes"]),  # type: ignore[arg-type]
        encrypted_content_hash=str(stored["encrypted_content_hash"]),
    )


def _encode_secret(material: bytes | None) -> str | None:
    # base64 in JSON for the same reason the parts keep both hashes: whoever
    # decrypts the package next reads this file and nothing else. `null` is the
    # spelling of a key that no longer exists — the field is never dropped, so a
    # record without one reads as deliberate rather than as truncation.
    return None if material is None else base64.b64encode(material).decode("ascii")


def _encode_pending(export: PendingExport) -> dict[str, object]:
    encryption = export.encryption
    return {
        "reference": export.reference,
        "direction": str(export.direction),
        "started_at": export.started_at.isoformat(),
        "state": str(export.state),
        "invoice_count": export.invoice_count,
        "covering_from": None if export.covering_from is None else export.covering_from.isoformat(),
        "encryption_key": _encode_secret(None if encryption is None else encryption.key),
        "initialisation_vector": _encode_secret(
            None if encryption is None else encryption.initialisation_vector
        ),
        "parts": [_encode_part(part) for part in export.parts],
    }


def _decode_encryption(stored: dict[str, object]) -> ExportEncryption | None:
    key = stored["encryption_key"]
    if key is None:
        return None
    return ExportEncryption(
        key=base64.b64decode(str(key)),
        initialisation_vector=base64.b64decode(str(stored["initialisation_vector"])),
    )


def _decode_pending(stored: dict[str, object]) -> PendingExport:
    # `.get`, not `[...]`: a record written before the field existed is still
    # this schema — an older reader ignores the key and an older document simply
    # does not carry it, so neither direction needs a version bump (GH-93).
    covering = stored.get("covering_from")
    return PendingExport(
        reference=str(stored["reference"]),
        direction=InvoiceDirection(stored["direction"]),
        started_at=datetime.fromisoformat(str(stored["started_at"])),
        encryption=_decode_encryption(stored),
        state=ExportState(stored["state"]),
        parts=tuple(_decode_part(part) for part in stored["parts"]),  # type: ignore[union-attr]
        invoice_count=int(stored["invoice_count"]),  # type: ignore[arg-type]
        covering_from=None if covering is None else datetime.fromisoformat(str(covering)),
    )


def _encode(state: SyncState, *, nip: str, environment: KsefEnvironment) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "nip": nip,
        "environment": str(environment),
        "continuation_points": {
            str(direction): {
                "reached": stored.reached.isoformat(),
                "attempted_at": None
                if stored.attempted_at is None
                else stored.attempted_at.isoformat(),
            }
            for direction, stored in state.directions.items()
        },
        "pending_exports": [_encode_pending(export) for export in state.pending],
        "settled_exports": [_encode_pending(export) for export in state.settled],
    }


def _decode_direction(stored: dict[str, object]) -> DirectionState:
    attempted = stored["attempted_at"]
    return DirectionState(
        reached=datetime.fromisoformat(str(stored["reached"])),
        attempted_at=None if attempted is None else datetime.fromisoformat(str(attempted)),
    )


def _decode(document: dict[str, object]) -> SyncState:
    found = document["schema_version"]
    if found != SCHEMA_VERSION:
        raise SyncStateUnreadable(
            f"Synchronisation state is schema {found}, this build reads "
            f"{SCHEMA_VERSION}. Refusing to guess: a misread continuation point "
            f"skips invoices nothing asks for again."
        )
    points: dict[str, dict[str, object]] = document["continuation_points"]  # type: ignore[assignment]
    exports: list[dict[str, object]] = document["pending_exports"]  # type: ignore[assignment]
    # `.get`, not `[...]`, for the same reason `covering_from` uses it: a record
    # written before the journal existed is still this schema, and an older
    # reader simply ignores the key — neither direction needs a version bump.
    settled: list[dict[str, object]] = document.get("settled_exports", [])  # type: ignore[assignment]
    queued = tuple(_decode_pending(export) for export in exports)
    return SyncState(
        directions={
            InvoiceDirection(direction): _decode_direction(stored)
            for direction, stored in points.items()
        },
        # A document written before the journal existed keeps its finished
        # exports in the queue — that is the deadlock GH-94 reports. They move
        # across on the first read rather than waiting for a pass to trip over
        # them, so the queue means one thing whatever wrote the file.
        pending=tuple(export for export in queued if not export.finished),
        settled=(
            *(_decode_pending(export) for export in settled),
            *(export for export in queued if export.finished),
        )[-SETTLED_JOURNAL_LIMIT:],
    )


@dataclass(frozen=True)
class SyncStore:
    """The per-subject, per-environment record. One file, written whole.

    Continuation points and queued exports share a single document on purpose:
    advancing a point is only safe while the parts that justify it are recorded,
    and one `rename(2)` is the only primitive that makes both true at once
    (D-006).
    """

    nip: str
    environment: KsefEnvironment
    root: Path | None = None

    @property
    def directory(self) -> Path:
        # Separate subdirectory per subject and per environment: a shared one is
        # the main vector for mixing an accounting office's clients (D-034), and
        # a test continuation point reused against production would declare a
        # period complete that was never fetched.
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.data_root(override=self.root)

    @property
    def path(self) -> Path:
        return self.directory / STATE_FILE

    def load(self) -> SyncState:
        if not self.path.is_file():
            return SyncState()
        return _decode(json.loads(self.path.read_text(encoding="utf-8")))

    @contextmanager
    def exclusively(self) -> Iterator[None]:
        """Hold this subject's directory for a whole read-change-write (ADR-107)."""
        with exclusive_write(self.directory, directory_mode=STATE_DIRECTORY_MODE):
            yield

    def updating(self, change: Callable[[SyncState], SyncState]) -> SyncState:
        """Read, change and write the record without another writer stepping in.

        The lock spans the cycle rather than the write, because the update a
        second writer loses is the one it read before that writer had finished
        (ADR-107 §2).
        """
        with self.exclusively():
            updated = change(self.load())
            self.save(updated)
            return updated

    def save(self, state: SyncState) -> Path:
        with self.exclusively():
            document = _encode(state, nip=self.nip, environment=self.environment)
            # temp → rename inside one directory (D-006): a crash mid-write
            # leaves the previous record intact, and a truncated continuation
            # point would cost the full resynchronisation this file exists to
            # prevent. The staging name is unique and the directory entry is
            # persisted after the swap (ADR-107 §3, §4).
            return written_atomically(
                self.path,
                content=(json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                file_mode=STATE_FILE_MODE,
            )
