import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from ksef_mcp import config, token_store
from ksef_mcp.allowance import Allowance
from ksef_mcp.archive import InvoiceArchive
from ksef_mcp.audit import (
    CSV_FORMAT,
    PDF_FORMAT,
    XML_FORMAT,
    AuditedOperation,
    AuditEntry,
    AuditTrail,
    Authorisation,
    AuthorisationBasis,
    Disclosure,
)
from ksef_mcp.diagnostics import (
    configure_diagnostics,
    correlated,
    correlation,
    requested_log_directory,
    technical_log,
)
from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.errors import KsefPortError
from ksef_mcp.ksef_port.types import InvoiceMetadata, Period
from ksef_mcp.listing import InvoiceLister, InvoiceListing, SubjectRoleListing
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.paths import Nip
from ksef_mcp.pdf import InvoiceRenderer, RenderedInvoice
from ksef_mcp.period_cache import PeriodCache
from ksef_mcp.review import InvoiceReview, InvoiceReviewer, ReviewStore, SubjectRoleReview
from ksef_mcp.statement import (
    STATEMENT_SUBJECT_ROLE,
    AccountingPeriod,
    Statement,
    StatementComposer,
    prepare_working_directory,
)
from ksef_mcp.sync_store import SyncStore
from ksef_mcp.synchronisation import SubjectRoleReport, SynchronisationReport, Synchroniser

if TYPE_CHECKING:
    from ksef_mcp.ksef_port.adapter import Ksef2Port

server: MCPServer = MCPServer(name=SERVER_NAME, version=VERSION)


class ServerInfo(BaseModel):
    name: str
    version: str


class SubjectRoleResult(BaseModel):
    """Paths and KSeF numbers for one subject type. Never an invoice body (D-011)."""

    subject_role: str
    outcome: str
    detail: str
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


class SynchronisationResult(BaseModel):
    environment: str
    subject_roles: list[SubjectRoleResult]
    pending_exports: list[str]
    state_file: str
    session_ceilings: SessionCeilingsResult
    # Quote this back and the whole pass can be read out of the journal
    # (GH-117). It is the one identifier here that names nobody.
    correlation: str


class NotConfigured(KsefMcpError):
    pass


# Refusals whose text was written for the person reading it: each names
# something the caller can act on — KSeF declining the call, a number not in the
# archive, a directory the server will not write to, a missing Node, a month it
# could not parse, a keyring collection that locked itself while the laptop
# slept — and none carries the NIP, the token or a line of invoice XML (D-011).
#
# Two roots rather than a hand-kept list of names. The list was the earlier
# design and its argument was that membership should cost a reviewer's glance;
# what it actually cost was three of five tools answering `Error executing tool`
# with the explaining sentence already written and thrown away (GH-167). The
# promise did not disappear — it moved to `KsefMcpError`, where the person
# writing the exception makes it, instead of to a tuple in another module that
# nobody was reminded to visit.
REFUSALS: Final[tuple[type[Exception], ...]] = (KsefPortError, KsefMcpError)


@contextmanager
def reported(operation: AuditedOperation) -> Iterator[None]:
    """Name the failure to the caller instead of letting the SDK swallow it.

    An exception the SDK does not recognise as anticipated reaches the client as
    a bare `Error executing tool <name>` and its text stays in a stderr the
    caller cannot see — which is how a schema mismatch in the limits response
    looked like a dead server (GH-76). `ToolError` is the SDK's channel for a
    failure we saw coming, so the reason travels with it.

    The refusals a tool raises about its own arguments belong here as much as
    the port's do (GH-84). Catching only the port left `render_invoice_pdf`
    answering "Error executing tool" for an invoice that was simply not
    synchronised yet and for a working directory it had declined — both
    outcomes its own docstring promises to explain.

    Enumerating those refusals one by one had the same failure a second time,
    and on the commonest path of all: a keyring collection locks itself when the
    machine suspends, four of the five tools read a token, and none of them
    reached `LOCKED_MESSAGE` (GH-167). The tuple is now two roots, so an
    exception written for a reader arrives without anyone remembering to list
    it.

    The refusal is journalled too (GH-116): a client that saw the sentence
    still leaves nothing an operator can reconstruct the pass from, and the
    sentence itself is gone as soon as the conversation moves on.

    This is also where a tool call acquires its identity (GH-117). Everything
    one call does to KSeF happens inside this block, so minting here and
    resetting on the way out is what lets the journal be read back as one
    sequence instead of eight rows sharing a `recorded_at`.
    """
    with correlated():
        try:
            yield
        except NotConfigured as error:
            technical_log().warning("%s refused: not configured. %s", operation, error)
            raise ToolError(
                f"{operation} needs configuration first. Run `ksef-mcp onboarding`. ({error})"
            ) from error
        except REFUSALS as error:
            # The message is safe to repeat here for the same reason it is safe
            # to send to the client: `KsefMcpError` promises it carries no
            # token, no invoice body and, since D-038, no KSeF number in full.
            technical_log().warning("%s refused: %s", operation, error)
            raise ToolError(f"{operation} could not finish. {error}") from error


@server.tool()
def server_info() -> ServerInfo:
    """Report the name and version of the running KSeF connector."""
    return ServerInfo(name=SERVER_NAME, version=VERSION)


def describe(
    report: SynchronisationReport, *, environment: config.KsefEnvironment
) -> SynchronisationResult:
    return SynchronisationResult(
        environment=str(environment),
        subject_roles=[
            SubjectRoleResult(
                subject_role=str(reported.subject_role),
                outcome=str(reported.outcome),
                detail=reported.detail,
                invoice_count=reported.invoice_count,
                part_count=reported.part_count,
                synchronised_up_to=(
                    None if reported.reached is None else reported.reached.isoformat()
                ),
                archived=list(reported.archived),
                already_held=list(reported.already_held),
                archive_directory=reported.archive_directory,
            )
            for reported in report.subject_roles
        ],
        pending_exports=list(report.pending_exports),
        state_file=report.state_path,
        session_ceilings=SessionCeilingsResult(
            assumed=report.ceilings.assumed,
            message=report.ceilings.message,
            max_invoice_megabytes=report.ceilings.max_invoice_megabytes,
            max_invoice_with_attachment_megabytes=(
                report.ceilings.max_invoice_with_attachment_megabytes
            ),
            max_invoices_per_session=report.ceilings.max_invoices_per_session,
        ),
        correlation=correlation(),
    )


def configured_subject() -> config.Configuration:
    """The configuration, with the NIP read in the one spelling we store it under.

    Normalised here rather than in `load_configuration`, which is a leaf every
    other module reads and must not learn about subject identity (GH-111). One
    spelling settled once is what keeps the keyring key and the directory name
    from drifting apart when the file was written with a grouped NIP.
    """
    configuration = config.load_configuration()
    if configuration is None:
        raise NotConfigured("Brak konfiguracji. Uruchom najpierw: ksef-mcp onboarding")
    return replace(configuration, nip=Nip.parsed(configuration.nip).value)


def authenticated_subject() -> tuple[config.Configuration, token_store.StoredToken]:
    """Which taxpayer we act as, and the secret that proves it. Never logged."""
    configuration = configured_subject()
    stored = token_store.read_token(nip=configuration.nip)
    if stored is None:
        raise NotConfigured(
            f"Brak tokenu dla {configuration.nip}. "
            f"Zapisz go: ksef-mcp token set --nip {configuration.nip}"
        )
    return configuration, stored


@dataclass(frozen=True)
class SubjectDependencies:
    """Everything a tool needs for one subject, built from one NIP.

    `Synchroniser` had already recognised this risk and derived its archive from
    its store rather than accept two arguments that must agree. Four tools
    assembled the port, the cache, the stores and the allowance by hand from the
    same pair of values, each free to drift from the others — a subject spelled
    one way for the cache and another for the allowance reads a period it never
    paid for (GH-114).

    Each access builds a fresh store. They are frozen dataclasses holding a NIP,
    an environment and an optional root, so there is nothing to share and
    nothing that would go stale between two tool calls.
    """

    configuration: config.Configuration

    @property
    def nip(self) -> str:
        return self.configuration.nip

    @property
    def environment(self) -> config.KsefEnvironment:
        return self.configuration.environment

    @property
    def port(self) -> "Ksef2Port":
        # Imported here rather than at module scope: `ksef2` pulls lxml, signxml
        # and xsdata, and a client listing tools must not pay half a second for
        # a dependency only the tools that reach KSeF ever touch.
        from ksef_mcp.ksef_port.adapter import Ksef2Port

        return Ksef2Port(environment=self.environment)

    @property
    def cache(self) -> PeriodCache:
        return PeriodCache(nip=self.nip, environment=self.environment)

    @property
    def archive(self) -> InvoiceArchive:
        return InvoiceArchive(nip=self.nip, environment=self.environment)

    @property
    def sync_store(self) -> SyncStore:
        return SyncStore(nip=self.nip, environment=self.environment)

    @property
    def review_store(self) -> ReviewStore:
        return ReviewStore(nip=self.nip, environment=self.environment)

    @property
    def allowance(self) -> Allowance:
        return Allowance(nip=self.nip, environment=self.environment)

    @property
    def trail(self) -> AuditTrail:
        return AuditTrail(nip=self.nip, environment=self.environment)

    def authorisation(self, stored: token_store.StoredToken) -> Authorisation:
        """The footing the read stands on — where the proof came from, never the proof."""
        return Authorisation(
            nip=self.nip,
            environment=self.environment,
            basis=AuthorisationBasis.KSEF_TOKEN,
            source=str(stored.source),
        )

    def archive_authorisation(self) -> Authorisation:
        """No token was read: the invoice was already on disk."""
        return Authorisation(
            nip=self.nip,
            environment=self.environment,
            basis=AuthorisationBasis.ARCHIVE,
        )


def authenticated_dependencies() -> tuple[SubjectDependencies, token_store.StoredToken]:
    configuration, stored = authenticated_subject()
    return SubjectDependencies(configuration=configuration), stored


def window_criteria(period: Period) -> str:
    """The query as asked, so a reader can tell scope from happenstance."""
    return f"{period.date_type} {period.date_from.isoformat()}..{period.date_to.isoformat()}"


def synchronisation_entries(
    report: SynchronisationReport,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    """One entry per subject type per outcome — stored, and seen but already held."""
    return tuple(
        entry
        for reported in report.subject_roles
        for entry in _subject_role_entries(
            reported,
            authorisation=authorisation,
            moment=moment,
        )
    )


def _subject_role_entries(
    reported: SubjectRoleReport,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    reached = "unknown" if reported.reached is None else reported.reached.isoformat()
    common = {
        "recorded_at": moment,
        "operation": AuditedOperation.SYNCHRONISATION,
        "authorisation": authorisation,
        "subject_role": str(reported.subject_role),
        "criteria": f"export packages up to {reached}",
        "output_path": reported.archive_directory,
    }
    written = (
        AuditEntry(
            disclosure=Disclosure.DISK,
            document_count=len(reported.archived),
            ksef_numbers=reported.archived,
            formats=(XML_FORMAT,),
            **common,  # type: ignore[arg-type]
        ),
    )
    # Logged as its own event rather than folded into the write or left out
    # altogether: a trail that records only what was stored would read as though
    # these invoices had never been touched, when in fact each one was seen and
    # correctly recognised as already held (#38, #57).
    skipped = (
        AuditEntry(
            disclosure=Disclosure.DEDUPLICATION_SKIP,
            document_count=len(reported.already_held),
            ksef_numbers=reported.already_held,
            formats=(),
            **common,  # type: ignore[arg-type]
        ),
    )
    return (
        *(written if reported.archived else ()),
        *(skipped if reported.already_held else ()),
    )


def synchronise() -> SynchronisationResult:
    subject, stored = authenticated_dependencies()
    synchroniser = Synchroniser(
        port=subject.port,
        store=subject.sync_store,
        allowance=subject.allowance,
    )
    report = synchroniser.run(nip=subject.nip, token=stored)
    trail = subject.trail
    trail.record(
        synchronisation_entries(
            report,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        )
    )
    return describe(report, environment=subject.environment)


@server.tool()
def synchronise_invoices() -> SynchronisationResult:
    """Fetch, decrypt and archive every invoice package KSeF finished since the last run.

    Takes no arguments on purpose. The date window, the package size and how
    many packages to ask for are decided by KSeF and by the hourly allowance,
    never by the caller: an agent driving them spends a twenty-per-hour budget
    in two minutes and the Ministry reads the pattern as working around a limit.

    Safe to call again. A package still being built, or one fetched but not yet
    stored, stays recorded on disk with its key, so a second call continues it
    instead of asking for it twice. An invoice already in the archive is
    reported under `already_held` rather than downloaded again.

    Reports where the invoices landed and which KSeF numbers arrived. It never
    returns invoice content: an FA(2)/FA(3) document holds a counterparty's
    personal data, and reading one means opening the file this tool names.

    `session_ceilings` says how much one session may carry and, in `assumed`,
    whether KSeF granted those figures or the conservative fallback is in force
    because the registry answered about limits in a shape that could not be
    read. An assumed ceiling is a different event from a granted one, and the
    message says so in as many words.

    `correlation` names this call in the technical journal on stderr. Quote it
    when reporting a failure: it is what lets the whole pass be reconstructed
    without running the synchronisation again out of twenty exports an hour.
    """
    with reported(AuditedOperation.SYNCHRONISATION):
        return synchronise()


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


class InvoiceListingResult(BaseModel):
    nip: str
    environment: str
    threshold: int
    period_from: str
    period_to: str
    subject_roles: list[SubjectRoleListingResult]


def describe_subject_role(listing: SubjectRoleListing) -> SubjectRoleListingResult:
    return SubjectRoleListingResult(
        subject_role=str(listing.question.subject_role),
        outcome=str(listing.outcome),
        message=listing.message,
        invoices=[
            InvoiceRow(
                ksef_number=str(invoice.ksef_number),
                seller_invoice_number=invoice.seller_invoice_number,
                issue_date=invoice.issue_date.isoformat(),
                seller_nip=invoice.seller_nip,
                seller_name=invoice.seller_name,
                buyer_name=invoice.buyer_name,
                gross_amount=invoice.gross_amount,
                net_amount=invoice.net_amount,
                vat_amount=invoice.vat_amount,
                currency=invoice.currency,
            )
            for invoice in listing.invoices
        ],
        invoice_count=listing.invoice_count,
        gross_totals=[
            GrossTotal(currency=total.currency, gross=total.gross) for total in listing.gross_totals
        ],
        complete=listing.complete,
        queried_at=None if listing.queried_at is None else listing.queried_at.isoformat(),
        from_cache=listing.from_cache,
    )


def describe_listing(listing: InvoiceListing) -> InvoiceListingResult:
    # Every subject type asked the same window, so the window is stated once at
    # the top rather than repeated four times.
    asked = listing.period
    return InvoiceListingResult(
        nip=listing.nip,
        environment=str(listing.environment),
        threshold=listing.threshold,
        period_from=asked.date_from.isoformat(),
        period_to=asked.date_to.isoformat(),
        subject_roles=[describe_subject_role(one) for one in listing.subject_roles],
    )


def listing_entries(
    listing: InvoiceListing,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    """What the model was shown, which is a different event from what was written.

    Recorded even for a subject type that returned nothing: the query was still
    made, and the scope of what was asked is half of what a dispute turns on.
    Above the listing threshold the rows never reach the answer, so the count
    stands alone with no numbers beside it — which is exactly what happened.
    """
    return tuple(
        AuditEntry(
            recorded_at=moment,
            operation=AuditedOperation.LISTING,
            authorisation=authorisation,
            disclosure=Disclosure.MODEL_CONTEXT,
            subject_role=str(listed.question.subject_role),
            criteria=window_criteria(listed.question.period),
            document_count=listed.invoice_count,
            ksef_numbers=tuple(str(invoice.ksef_number) for invoice in listed.invoices),
            output_path=None,
            formats=(),
        )
        for listed in listing.subject_roles
    )


def list_invoices() -> InvoiceListingResult:
    subject, stored = authenticated_dependencies()
    lister = InvoiceLister(
        port=subject.port,
        cache=subject.cache,
        allowance=subject.allowance,
    )
    listing = lister.run(nip=subject.nip, token=stored)
    trail = subject.trail
    trail.record(
        listing_entries(
            listing,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        )
    )
    return describe_listing(listing)


@server.tool()
def list_recent_invoices() -> InvoiceListingResult:
    """List the invoice metadata of the last thirty days, per subject type.

    Takes no arguments, for the same reason `synchronise_invoices` takes none:
    a window driven by the caller spends a twenty-per-hour metadata allowance in
    minutes, and the Ministry reads that pattern as working around a limit. The
    window ends on the hour, so asking again within the same hour is answered
    from disk and costs nothing.

    Reads only. There is no confirmation to click: a gate on a read teaches the
    person to approve without looking, and spends the gate that a write needs.

    At most fifty invoices are listed individually. Above that the rows are
    dropped and the answer carries the count and the gross total per currency
    instead — and `message` says so in as many words. An empty window is its own
    answer, restating the NIP, the environment, the subject type and both ends
    of the period, so "nothing arrived" can be told from "wrong question".

    Metadata only. An FA(2)/FA(3) body holds a counterparty's personal data and
    is third-party input; it never enters this answer.
    """
    with reported(AuditedOperation.LISTING):
        return list_invoices()


class StatementResult(BaseModel):
    """Where the file is and what has to be known before its sum is trusted."""

    nip: str
    environment: str
    period: str
    path: str
    row_count: int
    gross_totals: list[GrossTotal]
    complete: bool
    from_cache: bool
    queried_at: str
    message: str
    warnings: list[str]


def describe_statement(statement: Statement) -> StatementResult:
    return StatementResult(
        nip=statement.nip,
        environment=str(statement.environment),
        period=str(statement.period),
        path=statement.path,
        row_count=statement.row_count,
        gross_totals=[
            GrossTotal(currency=total.currency, gross=total.gross)
            for total in statement.gross_totals
        ],
        complete=statement.complete,
        from_cache=statement.from_cache,
        queried_at=statement.queried_at.isoformat(),
        message=statement.message,
        warnings=list(statement.warnings),
    )


def statement_entries(
    statement: Statement,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    """One entry, and it is a disk one — the rows leave in a file, not in the answer.

    `StatementResult` carries counts, sums and a path; no KSeF number of the
    period reaches the model through it. Recording a `MODEL_CONTEXT` event
    beside the write would therefore claim a disclosure that did not happen,
    and the D-011 distinction is worth only as much as its accuracy.
    """
    return (
        AuditEntry(
            recorded_at=moment,
            operation=AuditedOperation.STATEMENT,
            authorisation=authorisation,
            disclosure=Disclosure.DISK,
            subject_role=str(STATEMENT_SUBJECT_ROLE),
            criteria=str(statement.period),
            document_count=statement.row_count,
            ksef_numbers=statement.ksef_numbers,
            output_path=statement.path,
            formats=(CSV_FORMAT,),
        ),
    )


def export_statement(*, period: str, working_directory: str | None) -> StatementResult:
    subject, stored = authenticated_dependencies()
    directory = (
        subject.configuration.invoice_directory
        if working_directory is None
        else Path(working_directory).expanduser()
    )
    composer = StatementComposer(
        port=subject.port,
        cache=subject.cache,
        archive=subject.archive,
        allowance=subject.allowance,
    )
    statement = composer.run(
        nip=subject.nip,
        token=stored,
        period=AccountingPeriod.parsed(period),
        directory=directory,
    )
    trail = subject.trail
    trail.record(
        statement_entries(
            statement,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        )
    )
    return describe_statement(statement)


@server.tool()
def export_period_statement(
    period: str,
    working_directory: str | None = None,
) -> StatementResult:
    """Write one month of purchase invoices as a CSV an accountant can forward.

    `period` is a calendar month spelled `YYYY-MM`. The month is the unit a
    period is closed in, and both ends being fixed is what lets the same request
    be answered from disk instead of spending one of twenty metadata queries an
    hour a second time.

    `working_directory` overrides the directory declared during onboarding for
    this one call. Whichever is used is created `0700` if it is new, is refused
    outright if it lies inside the cache or data root — those are internal
    storage and deleting statements must never reach the archive — and is
    reported in `warnings` when its path looks like a cloud sync folder.

    The file holds ten columns, in this order: KSeF number, the seller's own
    invoice number, issue date, seller NIP, seller name, gross, net, VAT,
    currency, and a KOD I verification code. The code is composed from the
    seller NIP, the issue date and the SHA-256 of the archived invoice body;
    rows whose body is not in the archive yet say so instead of carrying a
    blank code.

    Addresses, bank accounts, invoice lines and local paths are absent on
    purpose: this file is written to be attached to an e-mail. The paths are in
    this answer instead.

    Amounts are written exactly as KSeF stated them — no rounding, no float
    anywhere on the path — with a comma as the decimal separator and a semicolon
    between fields, which is what a Polish spreadsheet expects. Sums are in this
    answer, per currency, and never one figure across several of them.
    """
    with reported(AuditedOperation.STATEMENT):
        return export_statement(period=period, working_directory=working_directory)


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


class InvoiceReviewResult(BaseModel):
    nip: str
    environment: str
    threshold: int
    period_from: str
    period_to: str
    ledger_file: str
    subject_roles: list[SubjectRoleReviewResult]


def reviewed_row(invoice: InvoiceMetadata) -> ReviewedInvoiceRow:
    return ReviewedInvoiceRow(
        ksef_number=str(invoice.ksef_number),
        seller_invoice_number=invoice.seller_invoice_number,
        issue_date=invoice.issue_date.isoformat(),
        seller_nip=invoice.seller_nip,
        seller_name=invoice.seller_name,
        buyer_name=invoice.buyer_name,
        gross_amount=invoice.gross_amount,
        net_amount=invoice.net_amount,
        vat_amount=invoice.vat_amount,
        currency=invoice.currency,
        received_on=invoice.ksef_number.assigned_on.isoformat(),
    )


def describe_review_subject_role(review: SubjectRoleReview) -> SubjectRoleReviewResult:
    return SubjectRoleReviewResult(
        subject_role=str(review.question.subject_role),
        outcome=str(review.outcome),
        message=review.message,
        new_invoices=[reviewed_row(invoice) for invoice in review.new_invoices],
        new_count=review.new_count,
        earlier_month_count=len(review.earlier_months),
        gross_totals=[
            GrossTotal(currency=total.currency, gross=total.gross) for total in review.gross_totals
        ],
        complete=review.complete,
        marked_as_reviewed=review.marked,
        queried_at=None if review.queried_at is None else review.queried_at.isoformat(),
        from_cache=review.from_cache,
    )


def describe_review(review: InvoiceReview) -> InvoiceReviewResult:
    asked = review.period
    return InvoiceReviewResult(
        nip=review.nip,
        environment=str(review.environment),
        threshold=review.threshold,
        period_from=asked.date_from.isoformat(),
        # Both ends are closed by construction (`review_period`), which is what
        # lets the period cache answer a repeat of this question for free.
        period_to=asked.date_to.isoformat(),
        ledger_file=review.ledger_path,
        subject_roles=[describe_review_subject_role(one) for one in review.subject_roles],
    )


def review_entries(
    review: InvoiceReview,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    """What the reader was shown as new, per subject type.

    `MODEL_CONTEXT` and no output path. The review ledger is written on every
    marked subject type, but it holds this server's own bookkeeping of what has
    already been reported, not the invoices — and the answer names it anyway,
    under `ledger_file`. Above the threshold the rows are dropped before the
    answer is composed, so the count stands with no numbers beside it, which is
    precisely what the reader saw.
    """
    return tuple(
        AuditEntry(
            recorded_at=moment,
            operation=AuditedOperation.REVIEW,
            authorisation=authorisation,
            disclosure=Disclosure.MODEL_CONTEXT,
            subject_role=str(assessed.question.subject_role),
            criteria=window_criteria(assessed.question.period),
            document_count=assessed.new_count,
            ksef_numbers=tuple(str(invoice.ksef_number) for invoice in assessed.new_invoices),
            output_path=None,
            formats=(),
        )
        for assessed in review.subject_roles
    )


def review_invoices() -> InvoiceReviewResult:
    subject, stored = authenticated_dependencies()
    reviewer = InvoiceReviewer(
        port=subject.port,
        cache=subject.cache,
        store=subject.review_store,
        allowance=subject.allowance,
    )
    review = reviewer.run(nip=subject.nip, token=stored)
    trail = subject.trail
    trail.record(
        review_entries(
            review,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        )
    )
    return describe_review(review)


@server.tool()
def review_new_invoices() -> InvoiceReviewResult:
    """Report the invoices that have arrived since a person was last shown any.

    This is the question the free Ministry application cannot answer: it lists
    what is there, never what is new since somebody last looked. The comparison
    is against a ledger of KSeF numbers already reported by this tool, kept on
    disk per subject and per environment, so it survives restarts.

    The window is the last ninety days, dated by acceptance in KSeF rather than
    by the seller's issue date — an invoice issued in March and accepted
    yesterday is new to the reader, and a window dated by issue date would not
    contain it. Both ends are fixed, so asking again within the same hour is
    answered from disk and spends nothing from twenty metadata queries an hour.

    `received_on` per invoice is the day KSeF assigned the number, read off the
    number itself. It is independent of when this tool fetched anything, and it
    is not the seller's issue date, which is reported separately.

    KSeF has no notion of a closed period and will not stop an invoice from
    landing in a month already filed, so `earlier_month_count` counts the new
    invoices whose number was assigned before the current month. That is a
    signal to look, never a statement that an invoice belongs to any period —
    the tax treatment is the reader's decision and this tool does not make it.

    Invoices listed individually are recorded as shown, and the next call does
    not repeat them. Above the threshold the rows are dropped, and then nothing
    is recorded, because nobody saw them — `marked_as_reviewed` says which of
    the two happened. Metadata only: an FA(2)/FA(3) body holds a counterparty's
    personal data and never enters this answer.
    """
    with reported(AuditedOperation.REVIEW):
        return review_invoices()


class RenderedInvoiceResult(BaseModel):
    """Where the document is and what it was made with. Never its content."""

    nip: str
    environment: str
    ksef_number: str
    path: str
    byte_count: int
    generator_version: str
    verification_url: str | None
    # A PDF carries the same counterparty personal data the CSV statement does,
    # so the working-directory caveats travel with it too (#172).
    warnings: list[str]


def describe_rendered(
    rendered: RenderedInvoice,
    *,
    nip: str,
    environment: config.KsefEnvironment,
    warnings: tuple[str, ...],
) -> RenderedInvoiceResult:
    return RenderedInvoiceResult(
        nip=nip,
        environment=str(environment),
        ksef_number=rendered.ksef_number,
        path=str(rendered.path),
        byte_count=rendered.byte_count,
        generator_version=rendered.generator_version,
        verification_url=rendered.verification_url,
        warnings=list(warnings),
    )


def render_entries(
    rendered: RenderedInvoice,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    """A disk disclosure of exactly one document, named by its number.

    The body reached a PDF on disk and nothing of it reached the model, so this
    is a `DISK` entry and not two (D-011). Nothing was fetched from KSeF either —
    the invoice was already held — which is why no criteria window is stated.
    """
    return (
        AuditEntry(
            recorded_at=moment,
            operation=AuditedOperation.RENDER,
            authorisation=authorisation,
            disclosure=Disclosure.DISK,
            subject_role=None,
            criteria=rendered.ksef_number,
            document_count=1,
            ksef_numbers=(rendered.ksef_number,),
            output_path=str(rendered.path),
            formats=(PDF_FORMAT,),
        ),
    )


def render_invoice(*, ksef_number: str, working_directory: str | None) -> RenderedInvoiceResult:
    subject = SubjectDependencies(configuration=configured_subject())
    directory = (
        subject.configuration.invoice_directory
        if working_directory is None
        else Path(working_directory).expanduser()
    )
    working = prepare_working_directory(directory)
    renderer = InvoiceRenderer(
        environment=subject.environment,
        archive_directory=subject.archive.invoice_directory,
        working_directory=working.path,
    )
    rendered = renderer(ksef_number)
    trail = subject.trail
    # No token was needed and none was read: the invoice was already on disk.
    # The trail still says under whose NIP and environment it was opened.
    trail.record(
        render_entries(
            rendered,
            authorisation=subject.archive_authorisation(),
            moment=trail.clock(),
        )
    )
    return describe_rendered(
        rendered,
        nip=subject.nip,
        environment=subject.environment,
        warnings=working.warnings,
    )


@server.tool()
def render_invoice_pdf(
    ksef_number: str,
    working_directory: str | None = None,
) -> RenderedInvoiceResult:
    """Write one archived invoice as the PDF the Ministry's own application shows.

    `ksef_number` names an invoice already in the archive. Nothing is fetched:
    this call spends none of the twenty metadata queries an hour and works with
    no network at all. An invoice that has not been synchronised yet is refused
    rather than downloaded, so the answer never depends on a query budget.

    The document is produced by the Ministry's own generator, run locally from a
    build vendored with this package — the same code the verification portal
    loads into a browser. Fidelity is therefore official rather than
    approximate, and `generator_version` names the build, the same string the
    footer of the document carries.

    `working_directory` overrides the directory declared during onboarding for
    this one call, under the same rules as the statement: created `0700` if new,
    refused inside the cache or data root, and reported in `warnings` when its
    path looks like a cloud sync folder or its permissions are wider than `0700`.
    The PDF names the counterparty exactly as the statement does.

    On production the document carries the QR code and verification link, and
    `verification_url` repeats it here. Test and demo invoices have no
    verification surface, so both are absent rather than pointing at a page that
    would not resolve.

    Requires Node — `uvx` cannot install it and a Python package cannot depend
    on it. Without Node this one call fails with a message saying what to
    install; synchronisation, the CSV statement and the listing are unaffected.

    The generator accepts FA(1), FA(2), FA(3), UPO and PEF, but only FA(3) has
    been exercised end to end. Treat a refusal on an older schema as untested
    rather than impossible, and report it.
    """
    with reported(AuditedOperation.RENDER):
        return render_invoice(ksef_number=ksef_number, working_directory=working_directory)


def main() -> None:
    configure_diagnostics(directory=requested_log_directory(os.environ))
    server.run()
