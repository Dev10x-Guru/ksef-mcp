"""What a synchronisation brought in, said out loud in the conversation.

The person reading a chat window cannot open the archive directory, and the
answer that names only a path leaves them with nothing to judge. So this module
turns invoice metadata into sentences — and everything hard about it is one
rule: a shortened list has to say that it was shortened (D-023). A tool caught
once showing fifty of eight hundred and twelve without saying so is a tool whose
every later number is worth nothing.

Three answers, never two. Above the threshold the rows are dropped and the count
and the gross total take their place; at or below it every row is listed; and an
empty window is its own answer, restating the question that produced it, so
"nothing here" can be told apart from "you asked the wrong thing".

Metadata only, never a body: the eight columns of `InvoiceMetadata` are what
D-023 settled, and an FA(2)/FA(3) document is third-party input that does not
enter the model's context (D-011). Reading is not gated behind a confirmation
either — a prompt on a read teaches the person to click "yes" without looking,
and spends the gate that writes actually need.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from ksef_mcp.allowance import Allowance
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.errors import KsefPortError, KsefRateLimited, KsefRequestRejected
from ksef_mcp.ksef_port.protocol import KsefPort
from ksef_mcp.ksef_port.types import (
    Credential,
    DateType,
    InvoiceMetadata,
    MetadataPage,
    Period,
    SubjectRole,
)
from ksef_mcp.period_cache import PeriodCache, PeriodMetadataReader
from ksef_mcp.synchronisation import SYNCHRONISED_SUBJECT_ROLES

# D-023, and the number is dimensioned against the need rather than against the
# API: a month in the studied archive is about thirteen invoices and a full
# quarter thirty-eight, so fifty is invisible in everyday work and cuts in only
# where the chat window would be unreadable anyway.
LISTING_THRESHOLD: Final[int] = 50

# The same thirty days `check_connection` uses, so "recently" means one thing in
# this project.
LISTING_WINDOW: Final[timedelta] = timedelta(days=30)

SUBJECT_ROLE_LABELS: Final[dict[SubjectRole, str]] = {
    SubjectRole.SELLER: "sprzedawca",
    SubjectRole.BUYER: "nabywca",
    SubjectRole.THIRD_SUBJECT: "podmiot trzeci",
    SubjectRole.AUTHORIZED_SUBJECT: "podmiot uprawniony",
}

DATE_TYPE_LABELS: Final[dict[DateType, str]] = {
    DateType.ISSUE: "data wystawienia",
    DateType.INVOICING: "data przyjęcia w KSeF",
    DateType.PERMANENT_STORAGE: "data trwałego zapisu",
}


# Two refusals, one consequence for the caller: this subject type went unasked,
# and the answers the allowance already bought stay. They are siblings and not
# parent and child in `ksef_port.errors`, so both have to be named — the local
# counter declining, and KSeF itself answering 429.
#
# Exactly these two. Widening to `KsefPortError` would swallow `KsefUnreachable`
# and `KsefAuthenticationFailed`, reporting a dead network or a rejected
# credential as "that subject type declined" and returning a cheerful partial
# answer where the caller needs to know nothing was asked at all.
AllowanceRefusal = KsefRequestRejected | KsefRateLimited

ALLOWANCE_REFUSALS: Final[tuple[type[KsefPortError], ...]] = (
    KsefRequestRejected,
    KsefRateLimited,
)


class ListingOutcome(StrEnum):
    LISTED = "listed"
    SUMMARISED = "summarised"
    EMPTY = "empty"
    BUDGET_SPENT = "budget_spent"


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def listing_period(*, moment: datetime) -> Period:
    """Thirty days ending on the hour — closed, so asking twice costs once.

    A listing dated like a synchronisation window would inherit the refusal
    `is_cacheable` documents, and asked again five minutes later would spend a
    second of twenty metadata queries an hour, times four subject types.
    Rounding the end down to the hour makes the window an identity the cache can
    match, and bounds staleness at the same hour KSeF settles its own allowance
    in. What it costs is visible rather than hidden: invoices from the hour in
    progress are outside the window, and the restated question says so by naming
    both ends.
    """
    ends = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return Period(date_from=ends - LISTING_WINDOW, date_to=ends, date_type=DateType.ISSUE)


def invoices_phrase(count: int) -> str:
    """Polish counts one, a few and many apart; getting it wrong reads as machine output."""
    if count == 1:
        return "1 faktura"
    tens = count % 100
    units = count % 10
    if units in (2, 3, 4) and tens not in (12, 13, 14):
        return f"{count} faktury"
    return f"{count} faktur"


@dataclass(frozen=True)
class CurrencyTotal:
    """One currency's gross sum. Never one number across several of them.

    Adding a złoty to a euro produces a figure nobody can defend, and this tool
    holds no exchange rate and has no business holding one.
    """

    currency: str
    gross: Decimal

    def __str__(self) -> str:
        return f"{self.gross} {self.currency}"


def gross_totals(invoices: tuple[InvoiceMetadata, ...]) -> tuple[CurrencyTotal, ...]:
    summed: dict[str, Decimal] = {}
    for invoice in invoices:
        summed[invoice.currency] = summed.get(invoice.currency, Decimal(0)) + invoice.gross_amount
    return tuple(
        CurrencyTotal(currency=currency, gross=summed[currency]) for currency in sorted(summed)
    )


def describe_totals(totals: tuple[CurrencyTotal, ...]) -> str:
    return ", ".join(str(total) for total in totals)


@dataclass(frozen=True)
class Question:
    """Exactly what was asked, so an empty answer can be judged rather than guessed at."""

    nip: str
    environment: KsefEnvironment
    subject_role: SubjectRole
    period: Period

    @property
    def restated(self) -> str:
        return (
            f"NIP {self.nip}, środowisko {self.environment}, "
            f"rola podmiotu: {SUBJECT_ROLE_LABELS[self.subject_role]}, "
            f"okres od {self.period.date_from.isoformat()} "
            f"do {self.period.date_to.isoformat()} "
            f"wg {DATE_TYPE_LABELS[self.period.date_type]}"
        )


@dataclass(frozen=True)
class SubjectRoleListing:
    """One subject type's answer, already worded for the chat window."""

    question: Question
    outcome: ListingOutcome
    message: str
    invoices: tuple[InvoiceMetadata, ...]
    invoice_count: int
    gross_totals: tuple[CurrencyTotal, ...]
    complete: bool
    queried_at: datetime | None
    from_cache: bool


@dataclass(frozen=True)
class InvoiceListing:
    nip: str
    environment: KsefEnvironment
    threshold: int
    period: Period
    subject_roles: tuple[SubjectRoleListing, ...]


def summarise(
    *,
    question: Question,
    page: MetadataPage,
    queried_at: datetime,
    from_cache: bool = False,
) -> SubjectRoleListing:
    """Turn one page into one of three answers, and never into a silent fourth."""
    invoices = page.invoices
    count = len(invoices)
    totals = gross_totals(invoices)
    complete = page.complete
    if count == 0:
        # A distinct answer, not an empty list: the question comes back with it,
        # because "nothing here" and "you asked about the wrong month" look
        # identical otherwise.
        return SubjectRoleListing(
            question=question,
            outcome=ListingOutcome.EMPTY,
            message=(
                f"Nie znalazłem żadnej faktury. Pytanie brzmiało — {question.restated}."
                f"{page.shortfall_note}"
            ),
            invoices=(),
            invoice_count=0,
            gross_totals=(),
            complete=complete,
            queried_at=queried_at,
            from_cache=from_cache,
        )
    if count > LISTING_THRESHOLD:
        return SubjectRoleListing(
            question=question,
            outcome=ListingOutcome.SUMMARISED,
            message=(
                f"Nie wypisuję pozycji: {invoices_phrase(count)} to więcej niż "
                f"próg {LISTING_THRESHOLD}. Podsumowanie okresu — "
                f"{invoices_phrase(count)}, brutto {describe_totals(totals)}. "
                f"Pytanie brzmiało — {question.restated}.{page.shortfall_note}"
            ),
            invoices=(),
            invoice_count=count,
            gross_totals=totals,
            complete=complete,
            queried_at=queried_at,
            from_cache=from_cache,
        )
    return SubjectRoleListing(
        question=question,
        outcome=ListingOutcome.LISTED,
        message=(
            f"{invoices_phrase(count)}, brutto {describe_totals(totals)}. "
            f"Wypisuję wszystkie — próg {LISTING_THRESHOLD} nie został "
            f"przekroczony.{page.shortfall_note}"
        ),
        invoices=invoices,
        invoice_count=count,
        gross_totals=totals,
        complete=complete,
        queried_at=queried_at,
        from_cache=from_cache,
    )


def describe_refusal(refusal: AllowanceRefusal) -> str:
    """The refusal in words, carrying KSeF's own wait when it gave one.

    Never a wait of this project's invention. Guessing how long to hold off is
    the pattern the Ministry answers with a lengthening block (D-017), so when
    the 429 came without `Retry-After`, the answer says nothing about waiting.
    """
    if not isinstance(refusal, KsefRateLimited) or refusal.retry_after is None:
        return str(refusal)
    return f"{refusal} KSeF prosi o odczekanie {refusal.retry_after} s."


def refused(*, question: Question, refusal: AllowanceRefusal) -> SubjectRoleListing:
    return SubjectRoleListing(
        question=question,
        outcome=ListingOutcome.BUDGET_SPENT,
        message=(
            f"Nie odpytałem KSeF-u o ten typ podmiotu: {describe_refusal(refusal)} "
            f"Pytanie brzmiało — {question.restated}."
        ),
        invoices=(),
        invoice_count=0,
        gross_totals=(),
        complete=False,
        queried_at=None,
        from_cache=False,
    )


@dataclass(frozen=True)
class InvoiceLister:
    """Reads the window for every subject type and words the answer.

    Every subject type, the same loop the synchronisation runs: a company is
    seller on one invoice and buyer on the next, and a listing covering one role
    would report a month as empty that is not (D-031 §5).
    """

    port: KsefPort
    cache: PeriodCache
    allowance: Allowance
    clock: Callable[[], datetime] = now_utc

    def run(self, *, nip: str, token: Credential) -> InvoiceListing:
        period = listing_period(moment=self.clock())
        listings: list[SubjectRoleListing] = []
        with self.port.session(nip=nip, token=token) as opened:
            session = self.allowance.guarded(session=opened)
            reader = PeriodMetadataReader(
                cache=self.cache,
                budget=self.allowance.budget(session=session),
            )
            for subject_role in SYNCHRONISED_SUBJECT_ROLES:
                question = Question(
                    nip=nip,
                    environment=self.port.environment,
                    subject_role=subject_role,
                    period=period,
                )
                try:
                    answer = reader.read(session=session, period=period, subject_role=subject_role)
                except ALLOWANCE_REFUSALS as refusal:
                    # One exhausted subject type must not take the other three
                    # with it: the answers already gathered are the ones the
                    # allowance was spent on. A real 429 costs the most to
                    # reach and used to be the one that threw them away (GH-95).
                    listings.append(refused(question=question, refusal=refusal))
                    continue
                listings.append(
                    summarise(
                        question=question,
                        page=answer.page,
                        queried_at=answer.queried_at,
                        from_cache=answer.from_cache,
                    )
                )
        return InvoiceListing(
            nip=nip,
            environment=self.port.environment,
            threshold=LISTING_THRESHOLD,
            period=period,
            subject_roles=tuple(listings),
        )
