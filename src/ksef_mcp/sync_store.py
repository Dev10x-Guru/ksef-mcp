"""Where synchronisation state lives between runs, and how it survives a crash.

Continuation points and queued exports go to the platformdirs *data* directory,
never the cache one: the cache convention is "safe to delete at any moment",
and a disk cleaner honouring it would silently cost a full resynchronisation
against an allowance of twenty exports an hour (D-032).
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Final

from platformdirs import user_data_path

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.types import (
    ContinuationPoint,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    InvoiceDirection,
)
from ksef_mcp.metadata import SERVER_NAME

STATE_FILE: Final[str] = "synchronisation.json"

SUBJECT_DIRECTORY: Final[str] = "subjects"

# Bumped whenever the document below stops being readable by the previous
# reader. A record written by a newer version is refused rather than guessed
# at: half-understood continuation points skip invoices, and nothing later
# asks for them again.
SCHEMA_VERSION: Final[int] = 1

STATE_DIRECTORY_MODE: Final[int] = 0o700

# The file carries the AES key of every queued export (D-033), so it is created
# with its final mode rather than written and then tightened.
STATE_FILE_MODE: Final[int] = 0o600


class SyncStateUnreadable(RuntimeError):
    """The record on disk was written by something this build does not understand."""


@dataclass(frozen=True)
class PendingExport:
    """A package KSeF has been asked for and this process has not yet archived.

    Persisted the moment the export is queued, because the export is
    asynchronous: the MCP server under `uvx` is routinely killed together with
    the agent session between the request and the parts becoming available, and
    a key held only in memory would burn one of twenty hourly exports (D-033).
    """

    reference: str
    direction: InvoiceDirection
    started_at: datetime
    encryption: ExportEncryption
    state: ExportState
    parts: tuple[ExportPart, ...] = ()
    invoice_count: int = 0

    @property
    def handle(self) -> ExportHandle:
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
    directions: dict[InvoiceDirection, DirectionState] = field(default_factory=dict)
    pending: tuple[PendingExport, ...] = ()

    def pending_for(self, direction: InvoiceDirection) -> PendingExport | None:
        return next((export for export in self.pending if export.direction == direction), None)

    def without_pending(self, *, reference: str) -> tuple[PendingExport, ...]:
        return tuple(export for export in self.pending if export.reference != reference)

    def with_pending(self, export: PendingExport) -> SyncState:
        return replace(self, pending=(*self.without_pending(reference=export.reference), export))

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


def _encode_pending(export: PendingExport) -> dict[str, object]:
    # The key and IV are base64 in JSON for the same reason the parts keep both
    # hashes: whoever decrypts the package next reads this file and nothing else.
    return {
        "reference": export.reference,
        "direction": str(export.direction),
        "started_at": export.started_at.isoformat(),
        "state": str(export.state),
        "invoice_count": export.invoice_count,
        "encryption_key": base64.b64encode(export.encryption.key).decode("ascii"),
        "initialisation_vector": base64.b64encode(export.encryption.initialisation_vector).decode(
            "ascii"
        ),
        "parts": [_encode_part(part) for part in export.parts],
    }


def _decode_pending(stored: dict[str, object]) -> PendingExport:
    return PendingExport(
        reference=str(stored["reference"]),
        direction=InvoiceDirection(stored["direction"]),
        started_at=datetime.fromisoformat(str(stored["started_at"])),
        encryption=ExportEncryption(
            key=base64.b64decode(str(stored["encryption_key"])),
            initialisation_vector=base64.b64decode(str(stored["initialisation_vector"])),
        ),
        state=ExportState(stored["state"]),
        parts=tuple(_decode_part(part) for part in stored["parts"]),  # type: ignore[union-attr]
        invoice_count=int(stored["invoice_count"]),  # type: ignore[arg-type]
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
    return SyncState(
        directions={
            InvoiceDirection(direction): _decode_direction(stored)
            for direction, stored in points.items()
        },
        pending=tuple(_decode_pending(export) for export in exports),
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
        base = user_data_path(appname=SERVER_NAME) if self.root is None else self.root
        return base / SUBJECT_DIRECTORY / self.nip / str(self.environment)

    @property
    def path(self) -> Path:
        return self.directory / STATE_FILE

    def load(self) -> SyncState:
        if not self.path.is_file():
            return SyncState()
        return _decode(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, state: SyncState) -> Path:
        self.directory.mkdir(mode=STATE_DIRECTORY_MODE, parents=True, exist_ok=True)
        document = _encode(state, nip=self.nip, environment=self.environment)
        # temp → rename inside one directory (D-006): a crash mid-write leaves
        # the previous record intact, and a truncated continuation point would
        # cost the full resynchronisation this file exists to prevent.
        staging = self.path.with_suffix(".tmp")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, STATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        staging.chmod(STATE_FILE_MODE)
        os.replace(staging, self.path)
        return self.path
