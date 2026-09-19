import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol, Self, runtime_checkable

from ksef_mcp.ksef_port.errors import InvalidKsefIdentifier, InvalidPeriod

# A KSeF number is `<NIP>-<YYYYMMDD>-<12 chars>-<2 chars>`. Only the first two
# groups are checked against their alphabet: the trailing groups are opaque to
# us, and a guard that outlives its own correctness rejects real invoices.
# What this has to catch is the seller's own invoice number arriving where the
# deduplication key belongs (D-005) — and that never has this shape.
KSEF_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{10}-\d{8}-[0-9A-Za-z]+-[0-9A-Za-z]+$"
)

KSEF_NUMBER_DATE_FORMAT: Final[str] = "%Y%m%d"

# `ksef2` enforces nothing on the date window, so without a cap here a too-wide
# window comes back as a server error spent from a 20-per-hour budget; the port
# refuses it for free instead (D-017).
#
# The number is the API's, not a client library's: MF answers windows up to
# three months, where D-017 had recorded the hundred days `ksef-client` caps at
# and a `[Verify]` against MF that the first production run settled (GH-84).
# The shortest three calendar months run to ninety days; the day below that is
# for skew, since the window is measured against KSeF's clock while `date_from`
# comes from ours.
MAX_QUERY_WINDOW: Final[timedelta] = timedelta(days=89)

# The API floor is 10 and the ceiling 250. Always the ceiling: naive paging at
# the SDK's default of 10 spends the hourly budget on 200 invoices (D-010).
# This is an invariant of the port, never a tuning knob reachable from a tool.
PAGE_SIZE: Final[int] = 250


@runtime_checkable
class Credential(Protocol):
    """A KSeF token that has not been unwrapped yet.

    Declared as a protocol rather than imported from `token_store`, so the port
    keeps its independence from the keyring — that separation is what ADR-102
    protects, and importing the store to name its type would undo it while
    changing nothing visible.

    A plain `str` does not satisfy it, and that is the point. The token used to
    be unwrapped at the server boundary and travel as a bare string through
    seven frames, so every network exception between here and there built a
    traceback with the secret in its locals (GH-115). `StoredToken` keeps its
    value out of `repr`; this is what lets that protection survive the journey.
    Unwrap it in the adapter, one line before handing it to the SDK, and nowhere
    earlier.
    """

    @property
    def value(self) -> str:
        """The secret itself. Read this only at the edge that spends it."""


class DateType(StrEnum):
    ISSUE = "issue_date"
    INVOICING = "invoicing_date"
    PERMANENT_STORAGE = "permanent_storage"


class SubjectRole(StrEnum):
    """Who the querying subject is on the invoice — required by the API.

    One concept, one name. It used to answer to four — `InvoiceDirection`,
    `direction`, `subject_role`, `subject_type` — two of them in a single line
    of the server. "Direction" was the worst of them: it promises two values
    while the set has four, and `AUTHORIZED_SUBJECT` is not a direction of
    anything. The glossary calls it the subject's role, so the code does too.

    The wire still speaks `Subject1..SubjectAuthorized` and the record on disk
    still spells the key `direction`; both are other people's vocabularies,
    translated at the boundary rather than adopted (D-017).
    """

    SELLER = "seller"
    BUYER = "buyer"
    THIRD_SUBJECT = "third_subject"
    AUTHORIZED_SUBJECT = "authorized_subject"


# The translation table D-017 asks for, written out rather than inferred. The
# SDK speaks roles, the wire speaks Subject*, and the two vocabularies drifting
# apart is exactly the kind of change this port exists to absorb.
WIRE_SUBJECT_TYPES: Final[dict[SubjectRole, str]] = {
    SubjectRole.SELLER: "Subject1",
    SubjectRole.BUYER: "Subject2",
    SubjectRole.THIRD_SUBJECT: "Subject3",
    SubjectRole.AUTHORIZED_SUBJECT: "SubjectAuthorized",
}


@dataclass(frozen=True)
class KsefNumber:
    value: str

    def __post_init__(self) -> None:
        if KSEF_NUMBER_PATTERN.match(self.value) is None:
            raise InvalidKsefIdentifier(
                f"Not a KSeF number: {self.value!r}. Expected "
                f"<NIP>-<YYYYMMDD>-<identifier>-<checksum>; the seller's own "
                f"invoice number is never a deduplication key."
            )
        # Eight digits is not yet a date, and `assigned_on` is the only record of
        # when an invoice reached KSeF (nothing on `InvoiceMetadata` carries it).
        # A number stating the thirty-first of February would otherwise fail far
        # from here, in the middle of reporting, rather than at the boundary.
        try:
            datetime.strptime(self.value.split("-")[1], KSEF_NUMBER_DATE_FORMAT)
        except ValueError as impossible:
            raise InvalidKsefIdentifier(
                f"KSeF number {self.value!r} states {self.value.split('-')[1]!r} "
                f"as the day it was assigned, and that is not a date."
            ) from impossible

    def __str__(self) -> str:
        return self.value

    @property
    def issued_for_nip(self) -> str:
        return self.value.split("-")[0]

    @property
    def assigned_on(self) -> date:
        """The day KSeF gave the invoice this number — its date of receipt.

        Read off the number rather than off a fetch timestamp, because those are
        different facts: an invoice assigned a number in July is a July arrival
        however long it took anyone to ask for it. The number is the only place
        this is stated; `InvoiceMetadata.issue_date` is the seller's own date.
        """
        return datetime.strptime(self.value.split("-")[1], KSEF_NUMBER_DATE_FORMAT).date()


@dataclass(frozen=True)
class Period:
    """A window with both ends, because an optional end is an unmeasured one.

    An absent `date_to` never meant an unbounded request — the adapter puts
    KSeF's `now` in its place — so it only moved the window past the ceiling
    below. A type that cannot express the unmeasured shape cannot leak it past
    a guard either, which is why the end is required rather than the guard
    taught to measure an open window against the clock (GH-84).
    """

    date_from: datetime
    date_to: datetime
    date_type: DateType

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise InvalidPeriod("The window ends before it starts.")
        if self.date_to - self.date_from > MAX_QUERY_WINDOW:
            raise InvalidPeriod(
                f"The window spans {(self.date_to - self.date_from).days} days; "
                f"KSeF answers windows up to {MAX_QUERY_WINDOW.days}. Split it."
            )

    @classmethod
    def for_synchronisation(cls, *, since: datetime, now: datetime) -> Self:
        """The window one pass asks for, closed so the guard above can see it.

        PermanentStorage by construction: any other date type makes incremental
        fetching unpredictable (D-031).

        A point further back than the ceiling gets a catch-up window instead of
        a refusal: the pass asks for the ceiling's worth from where it stands,
        and the next pass continues from the marker KSeF returns. Nothing is
        skipped — `restrict_to_permanent_storage_hwm_date` still stops the
        package at the point of completeness — and a subject dormant for a year
        catches up over runs rather than being stuck asking for a window that
        cannot be answered.
        """
        return cls(
            date_from=since,
            date_to=min(now, since + MAX_QUERY_WINDOW),
            date_type=DateType.PERMANENT_STORAGE,
        )


@dataclass(frozen=True)
class ContinuationPoint:
    """Where the next window for one subject type starts. Losing it costs a resync."""

    subject_role: SubjectRole
    reached: datetime

    def advanced_to(self, *, marker: datetime) -> Self:
        """Move to the marker the finished export names, keeping the subject type.

        Which of the export's two timestamps is the marker is the export's own
        question, answered by `ExportStatus.continuation_marker`. Asking it here
        as well put the D-031 §4 table in two places, and the caller then had to
        pass both timestamps with fallbacks — one of which could never fire.
        """
        return type(self)(subject_role=self.subject_role, reached=marker)


@dataclass(frozen=True)
class InvoiceMetadata:
    """The eight columns D-023 settled, plus what tells the taxpayer where it came from.

    Never the invoice body: FA(3) XML carries the counterparty's personal data
    and does not cross this boundary (D-011).
    """

    ksef_number: KsefNumber
    seller_invoice_number: str
    issue_date: date
    seller_nip: str
    seller_name: str | None
    buyer_name: str | None
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    currency: str


@dataclass(frozen=True)
class MetadataPage:
    """One page of a window, and where in the window it sits.

    `page_offset` is the SDK's zero-based page number, carried back so a caller
    can ask for the next one: without it `has_more` was reported and nothing
    could be done about it. A page assembled from several fetched ones carries
    the number of the last page it holds, so `page_offset + 1` is where a
    continuation resumes.

    `budget_bound` separates "incomplete because the allowance ran out" from
    "incomplete because KSeF said so" — two different pieces of news for the
    person reading the answer, and only the first one is worth waiting out. The
    adapter never sets it; it belongs to the reader that does the completing
    (ADR-109).
    """

    invoices: tuple[InvoiceMetadata, ...]
    has_more: bool
    truncated: bool
    hwm_date: datetime | None
    page_offset: int = 0
    budget_bound: bool = False

    @property
    def complete(self) -> bool:
        """Whether this page is the whole answer to the window it was asked for.

        The rule used to be spelled out at every reader, so a fifth signal of
        shortfall would have had to be added correctly in four places at once —
        and a missed one reports a shortened window as the whole truth, silently.
        It reads only this page's own fields, so it stays on the port's side of
        ADR-102.
        """
        return not (self.has_more or self.truncated)

    @property
    def shortfall_note(self) -> str:
        """The sentence a reader appends when the page is not the whole answer.

        Ready to append rather than ready to print: an answer that is complete
        must add nothing at all, and a note that carried its own leading space
        only at the call site is the same duplication one layer down.
        """
        if self.complete:
            return ""
        # KSeF's own shortening, and it falls under the same rule as ours: the
        # person has to hear about it, or the count beside it reads as the whole
        # truth about the window.
        return (
            " KSeF ma dla tego okna więcej faktur, niż zmieściło się w jednej "
            "odpowiedzi — to nie jest komplet."
        )


@dataclass(frozen=True)
class ExportEncryption:
    """AES-256 key and IV minted at export initialisation (D-031 §7, D-033)."""

    key: bytes
    initialisation_vector: bytes


@dataclass(frozen=True)
class ExportHandle:
    reference: str
    encryption: ExportEncryption


@dataclass(frozen=True)
class ExportPart:
    ordinal: int
    name: str
    method: str
    url: str
    size_bytes: int
    content_hash: str
    encrypted_size_bytes: int
    encrypted_content_hash: str


class ExportState(StrEnum):
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class ExportStatus:
    state: ExportState
    parts: tuple[ExportPart, ...]
    truncated: bool
    hwm_date: datetime | None
    last_permanent_storage_date: datetime | None
    invoice_count: int

    @property
    def continuation_marker(self) -> datetime | None:
        """Where the next window starts, or `None` when KSeF named no such point.

        The D-031 §4 table, and the only place it is read: adjoining windows,
        never overlapping, so a truncated package stops at the last invoice it
        carried and a complete one at the high water mark. Only one of the two
        fields is ever the real one — the other is inert, and a caller that read
        both had to invent a fallback for a case that cannot happen.

        `None` is the refusal, not a guess. A package whose marker KSeF left
        empty advances nowhere, because a guessed start skips invoices no later
        run ever asks for again.
        """
        return self.last_permanent_storage_date if self.truncated else self.hwm_date


class Operation(StrEnum):
    """The four operation families KSeF meters separately (D-031 §1).

    Declared beside `RateLimits` rather than beside the counter that spends
    against it, so the family and the allowance it maps to cannot be added in
    one place and forgotten in the other.
    """

    METADATA_QUERY = "metadata_query"
    EXPORT = "export"
    EXPORT_STATUS = "export_status"
    INVOICE_DOWNLOAD = "invoice_download"


@dataclass(frozen=True)
class OperationLimit:
    """What KSeF grants one operation family right now, not what we assumed."""

    per_second: int | None
    per_minute: int | None
    per_hour: int | None


@dataclass(frozen=True)
class RateLimits:
    """The allowances the budget counter spends against."""

    metadata_queries: OperationLimit
    exports: OperationLimit
    export_statuses: OperationLimit
    invoice_downloads: OperationLimit

    @property
    def by_operation(self) -> dict[Operation, OperationLimit]:
        """Which allowance each operation family spends from.

        The counter used to build this beside its own spending, so a fifth
        family meant a correct change in two independent types — and missing
        one raised `KeyError` in the middle of a synchronisation, which leaves
        the operation in a state nobody can read off the record afterwards. A
        test asserts this covers every member of `Operation`, so the omission
        fails in the suite instead of in a pass.
        """
        return {
            Operation.METADATA_QUERY: self.metadata_queries,
            Operation.EXPORT: self.exports,
            Operation.EXPORT_STATUS: self.export_statuses,
            Operation.INVOICE_DOWNLOAD: self.invoice_downloads,
        }

    def allowance(self, operation: Operation) -> OperationLimit:
        return self.by_operation[operation]


@dataclass(frozen=True)
class SessionCeilings:
    """How much one session may carry — sizes, not rates (D-031 §9)."""

    max_invoice_megabytes: int
    max_invoice_with_attachment_megabytes: int
    max_invoices_per_session: int


@dataclass(frozen=True)
class KsefLimits:
    rates: RateLimits
    ceilings: SessionCeilings
    # True when KSeF answered about limits in a shape we could not read and the
    # conservative fallback is in force. The counter still refuses at the
    # ceiling — this only says the ceiling is assumed rather than granted.
    degraded: bool = False
