"""What has arrived since a person was last shown anything."""

from ksef_mcp.invoices.review import (
    InvoiceReview,
    InvoiceReviewer,
    SubjectRoleReview,
    review_entries,
)
from ksef_mcp.ksef_port.types import InvoiceMetadata
from ksef_mcp.server.app import Journal, reported, server
from ksef_mcp.server.context import authenticated_dependencies
from ksef_mcp.server.results import (
    GrossTotal,
    InvoiceReviewResult,
    ReviewedInvoiceRow,
    SubjectRoleReviewResult,
)
from ksef_mcp.storage.audit import AuditedOperation


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


def describe_reviewed_total(review: InvoiceReview) -> str:
    counted = sum(one.new_count for one in review.subject_roles)
    earlier = sum(len(one.earlier_months) for one in review.subject_roles)
    return (
        f"Nowe faktury od ostatniego przeglądu: {counted}. "
        f"W tym z numerem sprzed bieżącego miesiąca: {earlier}. "
        f"Co widać dla każdej roli podmiotu z osobna — w `subject_roles`."
    )


def describe_review(review: InvoiceReview) -> InvoiceReviewResult:
    asked = review.period
    return InvoiceReviewResult(
        nip=review.nip,
        environment=str(review.environment),
        message=describe_reviewed_total(review),
        threshold=review.threshold,
        period_from=asked.date_from.isoformat(),
        # Both ends are closed by construction (`review_period`), which is what
        # lets the period cache answer a repeat of this question for free.
        period_to=asked.date_to.isoformat(),
        ledger_file=review.ledger_path,
        subject_roles=[describe_review_subject_role(one) for one in review.subject_roles],
    )


def review_invoices(*, journal: Journal) -> InvoiceReviewResult:
    subject, stored = authenticated_dependencies()
    reviewer = InvoiceReviewer(
        port=subject.port,
        cache=subject.cache,
        store=subject.review_store,
        allowance=subject.allowance,
    )
    review = reviewer.run(nip=subject.nip, token=stored)
    trail = subject.trail
    journal.record(
        trail=trail,
        entries=review_entries(
            review,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        ),
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
    with reported(AuditedOperation.REVIEW) as journal:
        return review_invoices(journal=journal)
