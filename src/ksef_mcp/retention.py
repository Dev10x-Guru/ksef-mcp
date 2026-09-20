"""Deleting invoice bodies without forgetting that they were ever held.

The archive does not expire on its own (D-034). That is a deliberate choice —
the local archive is the product's axis of value and what the Ministry expects
business operations to run against — and it was accepted on one condition: an
explicit command that frees the disk. Without it, a year later a laptop holds
every invoice of every subject an accounting office serves, contractors'
personal data included. This module is that safety valve, and it is part of
stage 1 rather than an addition to it.

What makes deleting safe is that the deduplication index is a separate file from
the invoices it describes (D-005). An `IndexEntry` outlives the body it was
written for, so after a purge the next synchronisation recognises those KSeF
numbers as already held and does not spend the twenty-exports-an-hour allowance
fetching them again. Nothing here writes to `deduplication.json`, and that
omission is the whole design: the index is the memory, the bodies are the cost.

Three other files live beside the archive and none of them is touched either.
`synchronisation.json` records how far KSeF has been *asked*; resetting it would
force a full resynchronisation. `review.json` records what a person has already
*been shown*, and that stays true however long the body survives. `audit.jsonl`
is the record of accesses, and it outlives retention of the invoices it
describes on purpose — a purge appends to it rather than trimming it.

Both dimensions, combinable, because both cases are real and excluding either
would be a guess (D-034). Per subject: an accounting office loses a client and
must remove that client's data, which is one subdirectory (D-032) and never a
neighbour's. Per period: a one-person business cuts by tax year. Together:
"client X's invoices older than a year".

The period is read off the KSeF number itself, whose `<YYYYMMDD>` group states
the day the invoice reached KSeF. Opening an FA(2)/FA(3) body to learn a date
already spelled in its file name would mean reading a contractor's personal data
for nothing (D-011). A file whose name is not a KSeF number is reported and left
where it is: an irreversible operation does not get to guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ksef_mcp.errors import KsefMcpInputRejected
from ksef_mcp.ksef_port.errors import InvalidKsefIdentifier
from ksef_mcp.ksef_port.types import KsefNumber
from ksef_mcp.storage.archive import INVOICE_SUFFIX, InvoiceArchive
from ksef_mcp.storage.audit import (
    XML_FORMAT,
    AuditedOperation,
    AuditEntry,
    Authorisation,
    Disclosure,
)


class PurgeWindowInverted(KsefMcpInputRejected):
    """The window ends before it begins, so it names no day at all.

    Refused at the boundary rather than silently deleting nothing: a command
    that reports "no invoices matched" for a typo teaches the operator that the
    archive is already clean when it is not.
    """


@dataclass(frozen=True)
class PurgeWindow:
    """Which days of receipt the purge covers. Both ends optional, both inclusive."""

    received_from: date | None = None
    received_to: date | None = None

    def __post_init__(self) -> None:
        if (
            self.received_from is not None
            and self.received_to is not None
            and self.received_from > self.received_to
        ):
            raise PurgeWindowInverted(
                f"The purge window starts on {self.received_from.isoformat()} and "
                f"ends on {self.received_to.isoformat()}, so it covers no day. "
                f"Refusing to report an empty archive for a swapped pair of dates."
            )

    def covers(self, day: date) -> bool:
        if self.received_from is not None and day < self.received_from:
            return False
        return self.received_to is None or day <= self.received_to

    @property
    def criteria(self) -> str:
        """The window as asked, for the audit trail and for the person reading it."""
        begins = "" if self.received_from is None else self.received_from.isoformat()
        ends = "" if self.received_to is None else self.received_to.isoformat()
        return f"received {begins}..{ends}"


@dataclass(frozen=True)
class PurgeCandidate:
    """One invoice body the window covers, named and measured before anything is deleted."""

    ksef_number: str
    received_on: date
    size_bytes: int
    path: Path


@dataclass(frozen=True)
class PurgePlan:
    """What the window would remove, stated in full before consent is asked for.

    Separate from the removal itself so the operator answers a question about
    real files rather than about a filter. `unrecognised` is part of the answer:
    a directory holding something this build cannot name is worth saying out loud
    at exactly the moment somebody is about to delete from it.
    """

    window: PurgeWindow
    directory: str
    index_path: str
    candidates: tuple[PurgeCandidate, ...]
    retained: tuple[str, ...]
    unrecognised: tuple[str, ...]

    @property
    def freed_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)


@dataclass(frozen=True)
class PurgeReport:
    """What is safe to say once the bodies are gone — numbers and paths, never content."""

    directory: str
    index_path: str
    criteria: str
    purged: tuple[str, ...]
    freed_bytes: int
    retained: tuple[str, ...]
    unrecognised: tuple[str, ...]
    still_known: int


def purge_entry(
    report: PurgeReport,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> AuditEntry:
    """The deletion, written down. Which numbers ceased to exist, under whose hand."""
    return AuditEntry(
        recorded_at=moment,
        operation=AuditedOperation.PURGE,
        authorisation=authorisation,
        disclosure=Disclosure.REMOVAL,
        subject_role=None,
        criteria=report.criteria,
        document_count=len(report.purged),
        ksef_numbers=report.purged,
        output_path=report.directory,
        formats=(XML_FORMAT,),
    )


def _number_of(path: Path) -> KsefNumber | None:
    try:
        return KsefNumber(path.stem)
    except InvalidKsefIdentifier:
        return None


@dataclass(frozen=True)
class ArchivePurge:
    """Removes invoice bodies from one subject's archive, and only from that one.

    Holds the archive rather than a path, so the directory it deletes from is the
    one the archive itself would write to — a purge computing its own path could
    drift from the writer and clean a directory nobody fills.
    """

    archive: InvoiceArchive

    def plan(self, *, window: PurgeWindow) -> PurgePlan:
        candidates: list[PurgeCandidate] = []
        retained: list[str] = []
        unrecognised: list[str] = []
        for path in sorted(self.archive.invoice_directory.glob(f"*{INVOICE_SUFFIX}")):
            number = _number_of(path)
            if number is None:
                unrecognised.append(path.name)
                continue
            received = number.assigned_on
            if not window.covers(received):
                retained.append(str(number))
                continue
            candidates.append(
                PurgeCandidate(
                    ksef_number=str(number),
                    received_on=received,
                    size_bytes=path.stat().st_size,
                    path=path,
                )
            )
        return PurgePlan(
            window=window,
            directory=str(self.archive.invoice_directory),
            index_path=str(self.archive.index_path),
            candidates=tuple(candidates),
            retained=tuple(retained),
            unrecognised=tuple(unrecognised),
        )

    def remove(self, *, plan: PurgePlan) -> PurgeReport:
        """Unlink every planned body. The index is read afterwards, never written."""
        for candidate in plan.candidates:
            candidate.path.unlink()
        # Read back rather than assumed: the sentence the operator is owed is
        # "the index still remembers these numbers", and only the file can say it.
        known = self.archive.load_index().known
        return PurgeReport(
            directory=plan.directory,
            index_path=plan.index_path,
            criteria=plan.window.criteria,
            purged=tuple(candidate.ksef_number for candidate in plan.candidates),
            freed_bytes=plan.freed_bytes,
            retained=plan.retained,
            unrecognised=plan.unrecognised,
            still_known=len(known),
        )
