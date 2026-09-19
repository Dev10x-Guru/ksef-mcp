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
record at risk to add one line. `fsync` follows every append, because evidence
that did not survive a power cut is not evidence.

`O_APPEND` places each write at the end of the file as it stands, and that is
all it does. A `write()` is indivisible against other appenders only up to
`PIPE_BUF`, so the claim that two processes acting for one subject cannot
interleave a line was true of short entries and false of a synchronisation
naming four subject types' invoices (GH-104). What actually serialises the
appenders is the subject's write lock (ADR-107 §5); `O_APPEND` remains because
the alternative to it is rewriting the whole trail on every line.
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

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.errors import KsefMcpError
from ksef_mcp.paths import SubjectScope
from ksef_mcp.storage import exclusive_write, require_schema

AUDIT_FILE: Final[str] = "audit.jsonl"

SCHEMA_VERSION: Final[int] = 1

AUDIT_DIRECTORY_MODE: Final[int] = 0o700

# The trail names KSeF numbers, and a KSeF number names the subject it was
# issued for (D-011), so the file is created with its final mode rather than
# written first and tightened after.
AUDIT_FILE_MODE: Final[int] = 0o600

XML_FORMAT: Final[str] = "xml"

CSV_FORMAT: Final[str] = "csv"

PDF_FORMAT: Final[str] = "pdf"

# What separates the source of a token from the basis itself in a recorded
# entry. The separator is part of the spelling already on disk, so it is named
# here rather than repeated inside a format string.
BASIS_SOURCE_SEPARATOR: Final[str] = ":"


class AuditTrailUnreadable(KsefMcpError):
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


class AuthorisationBasis(StrEnum):
    """What a read stood on. Three footings, and no fourth invented by a typo.

    A free `str` let a misspelled basis into the evidence without a word: the
    entry parsed, the trail read back, and the footing it named existed nowhere.
    The values are the ones already written to disk, so an enum here reads every
    trail this build has ever appended to and needs no `SCHEMA_VERSION` bump.

    `KSEF_TOKEN` is the only one that takes a source beside it — which keyring
    entry or environment variable the proof came from, never the proof.
    `OPERATOR` is deletion: it reaches no registry and needs no token, so a
    trail naming one would lie; what it stood on is a person at a terminal
    answering a question. `ARCHIVE` is a read that needed no token because the
    invoice was already held — spelled out rather than left blank, because a
    blank basis reads like a field nobody filled in.
    """

    KSEF_TOKEN = "ksef_token"
    OPERATOR = "operator:cli"
    ARCHIVE = "archiwum_lokalne"


class AuditedOperation(StrEnum):
    """Which operation a trail entry is about, named after the tool the caller invoked.

    That is the name the person reconstructing an access has in front of them.
    The six used to be `Final[str]` constants spread over two modules, so a
    typo in one of them recorded an operation that never existed and nothing —
    not the type checker, not the reader — could tell. The values are the
    strings already on disk, so this closes the set without a `SCHEMA_VERSION`
    bump on an append-only file (`Disclosure` was settled the same way).
    """

    SYNCHRONISATION = "synchronise_invoices"
    LISTING = "list_recent_invoices"
    STATEMENT = "export_period_statement"
    REVIEW = "review_new_invoices"
    RENDER = "render_invoice_pdf"
    PURGE = "purge_archive"


@dataclass(frozen=True)
class Authorisation:
    """Under whose NIP and on what footing a read happened. Never the secret itself."""

    nip: str
    environment: KsefEnvironment
    basis: AuthorisationBasis
    source: str | None = None

    @property
    def recorded(self) -> str:
        """The basis as one line of the trail spells it, with its source if it has one."""
        if self.source is None:
            return str(self.basis)
        return f"{self.basis}{BASIS_SOURCE_SEPARATOR}{self.source}"


@dataclass(frozen=True)
class AuditEntry:
    """One read, reconstructible: who, under what, asking what, getting which numbers."""

    recorded_at: datetime
    operation: AuditedOperation
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
        "operation": str(entry.operation),
        "nip": entry.authorisation.nip,
        "environment": str(entry.authorisation.environment),
        "authorisation_basis": entry.authorisation.recorded,
        "disclosure": str(entry.disclosure),
        "subject_role": entry.subject_role,
        "criteria": entry.criteria,
        "document_count": entry.document_count,
        "ksef_numbers": list(entry.ksef_numbers),
        "output_path": entry.output_path,
        "formats": list(entry.formats),
    }


def _decoded_basis(recorded: str) -> tuple[AuthorisationBasis, str | None]:
    """Split a recorded basis back into its footing and, where it has one, its source.

    Matched whole first, because `operator:cli` carries the separator inside the
    value itself. Splitting first would read it as a footing called `operator`
    with a source of `cli` — a different claim about the same entry, and one no
    reader would ever be warned about.
    """
    try:
        return AuthorisationBasis(recorded), None
    except ValueError:
        basis, _, source = recorded.partition(BASIS_SOURCE_SEPARATOR)
        return AuthorisationBasis(basis), source


def _decode(document: dict[str, object]) -> AuditEntry:
    require_schema(
        document,
        expected=SCHEMA_VERSION,
        named="An audit line",
        refused_as=AuditTrailUnreadable,
        consequence=(
            "a misread trail either invents an access that never happened or hides one that did."
        ),
    )
    role = document["subject_role"]
    path = document["output_path"]
    basis, source = _decoded_basis(str(document["authorisation_basis"]))
    return AuditEntry(
        recorded_at=datetime.fromisoformat(str(document["recorded_at"])),
        operation=AuditedOperation(str(document["operation"])),
        authorisation=Authorisation(
            nip=str(document["nip"]),
            environment=KsefEnvironment(str(document["environment"])),
            basis=basis,
            source=source,
        ),
        disclosure=Disclosure(str(document["disclosure"])),
        subject_role=None if role is None else str(role),
        criteria=str(document["criteria"]),
        document_count=int(document["document_count"]),  # type: ignore[arg-type]
        ksef_numbers=tuple(str(number) for number in document["ksef_numbers"]),  # type: ignore[union-attr]
        output_path=None if path is None else str(path),
        formats=tuple(str(name) for name in document["formats"]),  # type: ignore[union-attr]
    )


def _decoded_line(line: str, *, ordinal: int) -> AuditEntry:
    """One line of the trail, with a damaged one named rather than thrown raw.

    A `JSONDecodeError` escaping this module told the reader nothing about which
    file it came from or which line to look at, and it did not read as "the
    trail is damaged" at all (GH-104). The message names the position and
    nothing else: the line itself holds KSeF numbers, and an error message is
    the last place those should surface (D-011).
    """
    try:
        document = json.loads(line)
    except json.JSONDecodeError as damaged:
        raise AuditTrailUnreadable(
            f"Line {ordinal} of the audit trail is not JSON: {damaged.msg} at "
            f"position {damaged.pos}. A truncated or interleaved line means the "
            f"trail can no longer be read as evidence — keep the file and have "
            f"it examined rather than appending to it further."
        ) from damaged
    return _decode(document)


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
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.data_root(override=self.root)

    @property
    def path(self) -> Path:
        return self.directory / AUDIT_FILE

    def record(self, entries: Iterable[AuditEntry]) -> Path:
        """Append every entry as one indivisible block, or refuse to append at all.

        `O_APPEND` stays — this is a log, and the divergence from D-006 above is
        deliberate. What it never was is a serialisation mechanism: a single
        `write()` is indivisible only up to `PIPE_BUF`, and one
        `synchronise_invoices` naming four subject types' invoices passes four
        kilobytes without trying. Past that, two appenders interleave halves of
        a line and the trail stops parsing exactly where it is needed most
        (GH-104). The subject's write lock is what makes the block indivisible;
        the append-only shape is what the lock is protecting (ADR-107 §5).
        """
        lines = [json.dumps(_encode(entry), ensure_ascii=False) + "\n" for entry in entries]
        if not lines:
            return self.path
        with exclusive_write(self.directory, directory_mode=AUDIT_DIRECTORY_MODE):
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
            _decoded_line(line, ordinal=ordinal)
            for ordinal, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if line
        )
