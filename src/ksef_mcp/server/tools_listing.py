"""The last thirty days of invoice metadata, per subject type."""

from ksef_mcp.invoices.listing import (
    InvoiceLister,
    InvoiceListing,
    SubjectRoleListing,
    listing_entries,
)
from ksef_mcp.server.app import Journal, reported, server
from ksef_mcp.server.context import authenticated_dependencies
from ksef_mcp.server.results import (
    GrossTotal,
    InvoiceListingResult,
    InvoiceRow,
    SubjectRoleListingResult,
)
from ksef_mcp.storage.audit import AuditedOperation


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


def describe_listed_total(listing: InvoiceListing) -> str:
    counted = sum(one.invoice_count for one in listing.subject_roles)
    return (
        f"Faktury w oknie: {counted}. "
        f"Co widać dla każdej roli podmiotu z osobna — w `subject_roles`."
    )


def describe_listing(listing: InvoiceListing) -> InvoiceListingResult:
    # Every subject type asked the same window, so the window is stated once at
    # the top rather than repeated four times.
    asked = listing.period
    return InvoiceListingResult(
        nip=listing.nip,
        environment=str(listing.environment),
        message=describe_listed_total(listing),
        threshold=listing.threshold,
        period_from=asked.date_from.isoformat(),
        period_to=asked.date_to.isoformat(),
        subject_roles=[describe_subject_role(one) for one in listing.subject_roles],
    )


def list_invoices(*, journal: Journal) -> InvoiceListingResult:
    subject, stored = authenticated_dependencies()
    lister = InvoiceLister(
        port=subject.port,
        cache=subject.cache,
        allowance=subject.allowance,
    )
    listing = lister.run(nip=subject.nip, token=stored)
    trail = subject.trail
    journal.record(
        trail=trail,
        entries=listing_entries(
            listing,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        ),
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
    with reported(AuditedOperation.LISTING) as journal:
        return list_invoices(journal=journal)
