"""The shapes every tool answers in, declared once and in one place.

The client of these tools is a language model. What it can rely on is the shape
of the answer, so the shape is a contract and this module is where that contract
is written down — separately from the tools, which decide what to put in it.
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class ServerInfo(BaseModel):
    name: str
    version: str


class ToolResult(BaseModel):
    """The four fields every tool answers with, whichever tool was called.

    The client of an MCP tool is a language model, and it has no way to guess
    that the status sentence is `detail` in one answer, `message` in three and
    absent from the fifth — which is what it was before (GH-171). Declaring the
    shared shape once makes the guess unnecessary, and makes a tool that forgets
    a field a type error rather than a surprise in somebody's prompt.

    `nip` and `environment` say whose books were read and against which
    registry. They were on four of the five answers; the synchronisation one
    left the reader to infer the subject from a directory path.

    `warnings` is a caveat about the answer, not a failure — a failure leaves
    through `reported()` and never reaches a result at all. A tool with nothing
    to caution about answers with an empty list, which is a different statement
    from having no such field.
    """

    nip: str
    environment: str
    message: str
    warnings: list[str] = Field(default_factory=list)


class SubjectRoleResult(BaseModel):
    """Paths and KSeF numbers for one subject type. Never an invoice body (D-011)."""

    subject_role: str
    outcome: str
    message: str
    invoice_count: int
    part_count: int
    synchronised_up_to: str | None
    archived: list[str]
    already_held: list[str]
    archive_directory: str | None


class SessionCeilingsResult(BaseModel):
    """How much one session may carry, and whether KSeF granted it (GH-118)."""

    assumed: bool
    message: str
    max_invoice_megabytes: int
    max_invoice_with_attachment_megabytes: int
    max_invoices_per_session: int


class SynchronisationResult(ToolResult):
    subject_roles: list[SubjectRoleResult]
    pending_exports: list[str]
    state_file: str
    session_ceilings: SessionCeilingsResult
    # Quote this back and the whole pass can be read out of the journal
    # (GH-117). It is the one identifier here that names nobody.
    correlation: str


class InvoiceRow(BaseModel):
    """The eight columns D-023 settled, plus who the buyer was. Never a body (D-011)."""

    ksef_number: str
    seller_invoice_number: str
    issue_date: str
    seller_nip: str
    seller_name: str | None
    buyer_name: str | None
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    currency: str


class GrossTotal(BaseModel):
    currency: str
    gross: Decimal


class SubjectRoleListingResult(BaseModel):
    subject_role: str
    outcome: str
    message: str
    invoices: list[InvoiceRow]
    invoice_count: int
    gross_totals: list[GrossTotal]
    complete: bool
    queried_at: str | None
    from_cache: bool


class InvoiceListingResult(ToolResult):
    threshold: int
    period_from: str
    period_to: str
    subject_roles: list[SubjectRoleListingResult]


class StatementResult(ToolResult):
    """Where the file is and what has to be known before its sum is trusted."""

    period: str
    path: str
    row_count: int
    gross_totals: list[GrossTotal]
    complete: bool
    from_cache: bool
    queried_at: str


class ReviewedInvoiceRow(InvoiceRow):
    """An invoice row plus the day KSeF gave it its number — its date of receipt."""

    received_on: str


class SubjectRoleReviewResult(BaseModel):
    subject_role: str
    outcome: str
    message: str
    new_invoices: list[ReviewedInvoiceRow]
    new_count: int
    earlier_month_count: int
    gross_totals: list[GrossTotal]
    complete: bool
    marked_as_reviewed: bool
    queried_at: str | None
    from_cache: bool


class InvoiceReviewResult(ToolResult):
    threshold: int
    period_from: str
    period_to: str
    ledger_file: str
    subject_roles: list[SubjectRoleReviewResult]


class RenderedInvoiceResult(ToolResult):
    """Where the document is and what it was made with. Never its content.

    A PDF carries the same counterparty personal data the CSV statement does, so
    the working-directory caveats inherited in `warnings` travel with it too
    (#172).
    """

    ksef_number: str
    path: str
    byte_count: int
    generator_version: str
    verification_url: str | None
