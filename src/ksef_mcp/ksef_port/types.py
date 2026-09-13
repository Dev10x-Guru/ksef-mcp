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

# `ksef2` enforces nothing on the date window and `ksef-client` caps it at 100
# days client-side (D-017). Without a cap here a too-wide window comes back as
# a server error spent from a 20-per-hour budget, so the port refuses it for
# free instead. [Verify] whether MF documents the same ceiling.
MAX_QUERY_WINDOW: Final[timedelta] = timedelta(days=100)

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

    def __str__(self) -> str:
        return self.value

    @property
    def issued_for_nip(self) -> str:
        return self.value.split("-")[0]


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
    def for_synchronisation(cls, *, since: datetime) -> Self:
        # No upper bound and PermanentStorage by construction: MF builds the
        # largest consistent package it can, and any other date type makes
        # incremental fetching unpredictable (D-031).
        return cls(date_from=since, date_to=None, date_type=DateType.PERMANENT_STORAGE)


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
