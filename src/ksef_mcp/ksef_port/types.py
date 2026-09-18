import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self

from ksef_mcp.ksef_port.errors import KsefRequestRejected

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
# The number is the API's, not a client library's. D-017 carried the 100 days
# `ksef-client` caps at, with a `[Verify]` against MF — and the first production
# run answered it: `'dateRange' must not exceed 3 months`
# (VALIDATION_ERROR:21405, GH-84). The shortest three calendar months run to 90
# days, and the one day below that is deliberate: an open window is measured to
# KSeF's clock while `date_from` comes from ours, so a ceiling sitting exactly
# on the limit fails on skew and latency alone.
MAX_QUERY_WINDOW: Final[timedelta] = timedelta(days=89)

# The API floor is 10 and the ceiling 250. Always the ceiling: naive paging at
# the SDK's default of 10 spends the hourly budget on 200 invoices (D-010).
# This is an invariant of the port, never a tuning knob reachable from a tool.
PAGE_SIZE: Final[int] = 250


class DateType(StrEnum):
    ISSUE = "issue_date"
    INVOICING = "invoicing_date"
    PERMANENT_STORAGE = "permanent_storage"


class InvoiceDirection(StrEnum):
    """Who the querying subject is on the invoice — required by the API."""

    SELLER = "seller"
    BUYER = "buyer"
    THIRD_SUBJECT = "third_subject"
    AUTHORIZED_SUBJECT = "authorized_subject"


# The translation table D-017 asks for, written out rather than inferred. The
# SDK speaks roles, the wire speaks Subject*, and the two vocabularies drifting
# apart is exactly the kind of change this port exists to absorb.
WIRE_SUBJECT_TYPES: Final[dict[InvoiceDirection, str]] = {
    InvoiceDirection.SELLER: "Subject1",
    InvoiceDirection.BUYER: "Subject2",
    InvoiceDirection.THIRD_SUBJECT: "Subject3",
    InvoiceDirection.AUTHORIZED_SUBJECT: "SubjectAuthorized",
}


@dataclass(frozen=True)
class KsefNumber:
    value: str

    def __post_init__(self) -> None:
        if KSEF_NUMBER_PATTERN.match(self.value) is None:
            raise KsefRequestRejected(
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
            raise KsefRequestRejected(
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
class SubjectContext:
    """Which taxpayer we act as. A property of the credential, not of a query."""

    nip: str

    def __post_init__(self) -> None:
        if not self.nip.isdigit() or len(self.nip) != 10:
            raise KsefRequestRejected(f"A NIP is ten digits, got {len(self.nip)} characters.")


@dataclass(frozen=True)
class Period:
    date_from: datetime
    date_to: datetime | None
    date_type: DateType

    def __post_init__(self) -> None:
        if self.date_to is None:
            return
        if self.date_to < self.date_from:
            raise KsefRequestRejected("The window ends before it starts.")
        if self.date_to - self.date_from > MAX_QUERY_WINDOW:
            raise KsefRequestRejected(
                f"The window spans {(self.date_to - self.date_from).days} days; "
                f"KSeF answers windows up to {MAX_QUERY_WINDOW.days}. Split it."
            )

    @classmethod
    def for_synchronisation(cls, *, since: datetime, now: datetime) -> Self:
        """The window one pass asks for, closed so the guard above can see it.

        PermanentStorage by construction: any other date type makes incremental
        fetching unpredictable (D-031).

        The end is stated rather than left open, and that is the whole of GH-84.
        An open window is not unbounded on the wire — the adapter sends KSeF's
        `now` in its place — so leaving `date_to` unset only put the window past
        `__post_init__`, which returns early on `None`. The ceiling then guarded
        every window except the one synchronisation actually sends, and a first
        run reaching back further than KSeF answers was rejected as
        VALIDATION_ERROR:21405 with the export already spent.

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

    direction: InvoiceDirection
    reached: datetime

    def advanced_to(self, *, hwm: datetime, truncated: bool, last_seen: datetime) -> Self:
        # Adjoining windows, never overlapping: a truncated package stops at the
        # last invoice it carried, a complete one at the high water mark (D-031).
        return type(self)(
            direction=self.direction,
            reached=last_seen if truncated else hwm,
        )


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
    invoices: tuple[InvoiceMetadata, ...]
    has_more: bool
    truncated: bool
    hwm_date: datetime | None


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
