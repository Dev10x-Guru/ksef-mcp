from decimal import Decimal

from mcp.server import MCPServer
from pydantic import BaseModel

from ksef_mcp import config, token_store
from ksef_mcp.listing import DirectionListing, InvoiceLister, InvoiceListing
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.period_cache import PeriodCache
from ksef_mcp.sync_store import SyncStore
from ksef_mcp.synchronisation import SynchronisationReport, Synchroniser

server: MCPServer = MCPServer(name=SERVER_NAME, version=VERSION)


class ServerInfo(BaseModel):
    name: str
    version: str


class SubjectTypeResult(BaseModel):
    """Paths and KSeF numbers for one subject type. Never an invoice body (D-011)."""

    subject_type: str
    outcome: str
    detail: str
    invoice_count: int
    part_count: int
    synchronised_up_to: str | None
    archived: list[str]
    already_held: list[str]
    archive_directory: str | None


class SynchronisationResult(BaseModel):
    environment: str
    subject_types: list[SubjectTypeResult]
    pending_exports: list[str]
    state_file: str


class NotConfigured(RuntimeError):
    pass


@server.tool()
def server_info() -> ServerInfo:
    """Report the name and version of the running KSeF connector."""
    return ServerInfo(name=SERVER_NAME, version=VERSION)


def describe(
    report: SynchronisationReport, *, environment: config.KsefEnvironment
) -> SynchronisationResult:
    return SynchronisationResult(
        environment=str(environment),
        subject_types=[
            SubjectTypeResult(
                subject_type=str(direction.direction),
                outcome=str(direction.outcome),
                detail=direction.detail,
                invoice_count=direction.invoice_count,
                part_count=direction.part_count,
                synchronised_up_to=(
                    None if direction.reached is None else direction.reached.isoformat()
                ),
                archived=list(direction.archived),
                already_held=list(direction.already_held),
                archive_directory=direction.archive_directory,
            )
            for direction in report.directions
        ],
        pending_exports=list(report.pending_exports),
        state_file=report.state_path,
    )


def authenticated_subject() -> tuple[config.Configuration, str]:
    """Which taxpayer we act as, and the secret that proves it. Never logged."""
    configuration = config.load_configuration()
    if configuration is None:
        raise NotConfigured("Brak konfiguracji. Uruchom najpierw: ksef-mcp onboarding")
    stored = token_store.read_token(nip=configuration.nip)
    if stored is None:
        raise NotConfigured(
            f"Brak tokenu dla {configuration.nip}. "
            f"Zapisz go: ksef-mcp token set --nip {configuration.nip}"
        )
    return configuration, stored.value


def synchronise() -> SynchronisationResult:
    # Imported here rather than at module scope: `ksef2` pulls lxml, signxml and
    # xsdata, and a client listing tools must not pay half a second for a
    # dependency only this tool reaches for.
    from ksef_mcp.ksef_port.adapter import Ksef2Port

    configuration, token = authenticated_subject()
    synchroniser = Synchroniser(
        port=Ksef2Port(environment=configuration.environment),
        store=SyncStore(nip=configuration.nip, environment=configuration.environment),
    )
    return describe(
        synchroniser.run(nip=configuration.nip, token=token),
        environment=configuration.environment,
    )


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
    """
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


class DirectionListingResult(BaseModel):
    subject_type: str
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
    period_to: str | None
    subject_types: list[DirectionListingResult]


def describe_direction(listing: DirectionListing) -> DirectionListingResult:
    return DirectionListingResult(
        subject_type=str(listing.question.direction),
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
        period_to=None if asked.date_to is None else asked.date_to.isoformat(),
        subject_types=[describe_direction(one) for one in listing.directions],
    )


def list_invoices() -> InvoiceListingResult:
    from ksef_mcp.ksef_port.adapter import Ksef2Port

    configuration, token = authenticated_subject()
    lister = InvoiceLister(
        port=Ksef2Port(environment=configuration.environment),
        cache=PeriodCache(nip=configuration.nip, environment=configuration.environment),
    )
    return describe_listing(lister.run(nip=configuration.nip, token=token))


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
    return list_invoices()


def main() -> None:
    server.run()
