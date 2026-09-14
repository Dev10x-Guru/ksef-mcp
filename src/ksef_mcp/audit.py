"""The permanent record of every read, because in a leak dispute it is the only one.

Compliance names one risk above the others: an agent fetches customer A's
invoices while answering a question about customer B. KSeF will never report
that as an error — the permission genuinely exists, the call is well formed, and
the API answers it. Nothing in the protocol distinguishes a read that was asked
for from a read that was not. What separates them is the record of who acted
under which NIP, on what footing, against which criteria, and which KSeF numbers
came back, and that record has to be written here or it does not exist.

Four kinds of event, kept apart on purpose (D-011). `DISK` says documents
landed in a file. `MODEL_CONTEXT` says metadata entered a tool's answer and the
model therefore saw it — "the chat was shown twelve" is a different event from
"twelve files were written", and folding them together loses exactly the
distinction D-011 draws. `DEDUPLICATION_SKIP` says an invoice was seen and
recognised as already held; a trail carrying only writes would read as though
that invoice had never been touched at all. `REMOVAL` says the bodies were
deleted by an explicit purge (D-034) — the one event where nobody saw anything,
and the one whose record has to survive the invoices it names.

The trail is written whether or not anything asked a human for permission. A
read needs no confirmation gate (D-011) and still needs a record: consent and
accountability are separate concerns and coupling them would delete the second
one the moment the first was decided against.

Numbers, never names, and never a body. A KSeF number identifies an invoice
completely for the purpose of reconstructing an access, and it says nothing
about the counterparty beyond a NIP already stated. Minimising personal data
inside the log is not decoration: the audit trail outlives retention of the
invoices it describes.

Appended, never rewritten — and that is a deliberate divergence from D-006. The
`temp → rename` pattern that guards `InvoiceArchive` and `ReviewStore` protects
a whole-file invariant: those files are read entire, and half a document reads
back as an empty one. A trail is the other shape. It only ever grows, nobody
edits its middle, and rewriting it whole on every append would both cost
quadratically over a server running for weeks and put the entire accumulated
record at risk to add one line. A single `write()` on an `O_APPEND` descriptor
is indivisible against other appenders, so two processes acting for the same
subject cannot interleave a line, and `fsync` follows each one because evidence
that did not survive a power cut is not evidence.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from platformdirs import user_data_path

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.sync_store import SUBJECT_DIRECTORY

AUDIT_FILE: Final[str] = "audit.jsonl"

SCHEMA_VERSION: Final[int] = 1

AUDIT_DIRECTORY_MODE: Final[int] = 0o700

# The trail names KSeF numbers, and a KSeF number names the subject it was
# issued for (D-011), so the file is created with its final mode rather than
# written first and tightened after.
AUDIT_FILE_MODE: Final[int] = 0o600

TOKEN_BASIS: Final[str] = "ksef_token"

# Deletion reaches no registry and needs no token, so the trail would lie if it
# named one. What the operation stood on is a person at a terminal answering a
# question, and that is what the entry says.
OPERATOR_BASIS: Final[str] = "operator:cli"

XML_FORMAT: Final[str] = "xml"

CSV_FORMAT: Final[str] = "csv"


class AuditTrailUnreadable(RuntimeError):
    """A line of the trail was written by a build this one does not understand.

    Refused rather than guessed at. A trail read through the wrong schema either
    invents an access that never happened or hides one that did, and both are
    worse in a dispute than admitting the file cannot be read.
    """


class Disclosure(StrEnum):
    """What became of the invoices — the D-011 distinction, made explicit per entry.

    The first three say where content went. `REMOVAL` is the fourth answer to the
    same question and the only one pointing the other way: the bodies ceased to
    exist, and nobody was shown anything (D-034). It is a value of this enum
    rather than a field of its own because a new field would bump
    `SCHEMA_VERSION`, and that would make this build refuse every trail written
    before it — a file that is only ever appended to and never rewritten.
    """

    DISK = "disk"
    MODEL_CONTEXT = "model_context"
    DEDUPLICATION_SKIP = "deduplication_skip"
    REMOVAL = "removal"


@dataclass(frozen=True)
class Authorisation:
    """Under whose NIP and on what footing a read happened. Never the secret itself."""

    nip: str
    environment: KsefEnvironment
    basis: str


def token_basis(source: object) -> str:
    """Where the proof of authorisation came from, spelled without the proof."""
    return f"{TOKEN_BASIS}:{source}"


@dataclass(frozen=True)
class AuditEntry:
    """One read, reconstructible: who, under what, asking what, getting which numbers."""

    recorded_at: datetime
    operation: str
    authorisation: Authorisation
    disclosure: Disclosure
    subject_role: str | None
    criteria: str
    document_count: int
    ksef_numbers: tuple[str, ...]
    output_path: str | None
    formats: tuple[str, ...]


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _encode(entry: AuditEntry) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": entry.recorded_at.isoformat(),
        "operation": entry.operation,
        "nip": entry.authorisation.nip,
        "environment": str(entry.authorisation.environment),
        "authorisation_basis": entry.authorisation.basis,
        "disclosure": str(entry.disclosure),
        "subject_role": entry.subject_role,
        "criteria": entry.criteria,
        "document_count": entry.document_count,
        "ksef_numbers": list(entry.ksef_numbers),
        "output_path": entry.output_path,
        "formats": list(entry.formats),
    }


def _decode(document: dict[str, object]) -> AuditEntry:
    found = document["schema_version"]
    if found != SCHEMA_VERSION:
        raise AuditTrailUnreadable(
            f"An audit line is schema {found}, this build reads {SCHEMA_VERSION}. "
            f"Refusing to guess: a misread trail either invents an access that "
            f"never happened or hides one that did."
        )
    role = document["subject_role"]
    path = document["output_path"]
    return AuditEntry(
        recorded_at=datetime.fromisoformat(str(document["recorded_at"])),
        operation=str(document["operation"]),
        authorisation=Authorisation(
            nip=str(document["nip"]),
            environment=KsefEnvironment(str(document["environment"])),
            basis=str(document["authorisation_basis"]),
        ),
        disclosure=Disclosure(str(document["disclosure"])),
        subject_role=None if role is None else str(role),
        criteria=str(document["criteria"]),
        document_count=int(document["document_count"]),  # type: ignore[arg-type]
        ksef_numbers=tuple(str(number) for number in document["ksef_numbers"]),  # type: ignore[union-attr]
        output_path=None if path is None else str(path),
        formats=tuple(str(name) for name in document["formats"]),  # type: ignore[union-attr]
    )


@dataclass(frozen=True)
class AuditTrail:
    """One subject's record of reads, beside the archive whose accesses it describes."""

    nip: str
    environment: KsefEnvironment
    root: Path | None = None
    clock: Callable[[], datetime] = now_utc

    @property
    def directory(self) -> Path:
        # The data root, never the cache one (D-032): a disk cleaner honouring
        # the cache convention would delete the evidence, and per subject and per
        # environment like everything beside it, because one accounting office's
        # clients sharing a trail is the mixing vector D-034 names — here it
        # would also be the leak the trail exists to prove did not happen.
        base = user_data_path(appname=SERVER_NAME) if self.root is None else self.root
        return base / SUBJECT_DIRECTORY / self.nip / str(self.environment)

    @property
    def path(self) -> Path:
        return self.directory / AUDIT_FILE

    def record(self, entries: Iterable[AuditEntry]) -> Path:
        lines = [json.dumps(_encode(entry), ensure_ascii=False) + "\n" for entry in entries]
        if not lines:
            return self.path
        self.directory.mkdir(mode=AUDIT_DIRECTORY_MODE, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, AUDIT_FILE_MODE)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        return self.path

    def entries(self) -> tuple[AuditEntry, ...]:
        """Read the trail back, for the person who has to reconstruct an access."""
        if not self.path.is_file():
            return ()
        return tuple(
            _decode(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line
        )
