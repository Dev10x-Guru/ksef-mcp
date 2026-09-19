"""What has arrived since a person last looked — the one thing the free app cannot do.

A test against a real taxpayer's purchase invoices found four of thirty-eight
missing from a manually kept archive (D-025). Three were the current month's
backlog. The fourth was assigned its KSeF number on the seventh of July, two
hundred and fifteen złoty, and July had been archived weeks earlier: it fell
outside the supplier's usual rhythm, so nobody thought to look for it. Manual
archiving has no mechanism that says "something is missing", and that absence —
not the amount — is what this module answers.

Three markers are easy to confuse, and this one is the third. `SyncStore` keeps
the continuation point: how far KSeF has been *asked*. `DeduplicationIndex`
keeps `archived_at` per KSeF number: which invoice bodies are *held*. Neither
says whether a *person* has been shown an invoice, and that is the marker D-022
asks for — the one that turns a repeated query into "new since last time".

It gets its own file beside the other two rather than a column in the
deduplication index, for a reason that is about correctness and not about taste.
An invoice reported as new is typically one the archive does not hold yet — that
is the whole ST-4 case, the metadata query seeing what the archive missed.
Writing it into the deduplication index would make `known` claim the body is
held, and the next synchronisation would skip fetching it. The two markers also
outlive each other differently: retention deletes bodies, and what a person has
already reviewed stays true regardless.

It lives in the data root, never the cache one (D-032). Losing a cached period
costs one query; losing this costs the record of what somebody has already been
shown, and the invoice from the seventh of July would quietly become new again.

The date of receipt is read off the KSeF number itself. `InvoiceMetadata`
carries no timestamp of registration — `issue_date` is the seller's own date and
`MetadataPage.hwm_date` describes a page rather than an invoice — so the
`<YYYYMMDD>` group of the number is the only statement of when the invoice
reached KSeF, and it is independent of when this tool happened to fetch it.

Nothing here decides anything. KSeF has no notion of a closed period and will
not stop an invoice from landing in a month that is already filed; the answer
says an invoice is new and when it was assigned its number, and never that it
belongs to a period. That judgement is the accountant's (ST-4).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final

from platformdirs import user_data_path

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.budget import QueryBudget
from ksef_mcp.ksef_port.protocol import KsefPort
from ksef_mcp.ksef_port.types import MAX_QUERY_WINDOW, DateType, InvoiceMetadata, Period
from ksef_mcp.listing import (
    ALLOWANCE_REFUSALS,
    LISTING_THRESHOLD,
    AllowanceRefusal,
    CurrencyTotal,
    Question,
    describe_refusal,
    describe_totals,
    gross_totals,
    invoices_phrase,
)
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.period_cache import PeriodCache, PeriodMetadataReader
from ksef_mcp.sync_store import SUBJECT_DIRECTORY
from ksef_mcp.synchronisation import SYNCHRONISED_DIRECTIONS

REVIEW_FILE: Final[str] = "review.json"

STAGING_SUFFIX: Final[str] = ".tmp"

SCHEMA_VERSION: Final[int] = 1

REVIEW_DIRECTORY_MODE: Final[int] = 0o700

# The ledger names KSeF numbers, and a KSeF number names the subject it was
# issued for (D-011), so the file is created with its final mode.
REVIEW_FILE_MODE: Final[int] = 0o600

# Dimensioned against the evidence, then capped by the API. The overlooked
# invoice in the study was three months old when the comparison found it, so the
# thirty days `list_recent_invoices` covers would have walked straight past it;
# the same study puts a full quarter at thirty-eight invoices, under the listing
# threshold. Ninety days is that evidence, and it stays written here as its own
# decision — the cap is a separate fact, and folding the two into one constant
# would let a future change to MF's limit silently redefine what "new since last
# time" means. Today the cap binds, because ninety days is one day over what
# KSeF answers (GH-84).
REVIEW_WINDOW: Final[timedelta] = min(timedelta(days=90), MAX_QUERY_WINDOW)

# Dated by acceptance in KSeF, not by the seller's issue date, and that is the
# load-bearing choice. An invoice issued in March and accepted yesterday is new
# to the person looking; a window dated by issue date would not contain it at
# all, and ST-4 is exactly that shape. It also matches what the answer reports
# per invoice, since `KsefNumber.assigned_on` states the same fact.
REVIEW_DATE_TYPE: Final[DateType] = DateType.INVOICING


class ReviewLedgerUnreadable(RuntimeError):
    """The record of what a person has seen was written by an unknown build.

    Refused rather than guessed at, the way `SyncStore` refuses a continuation
    point and unlike the period cache, which shrugs. A misread ledger either
    replays invoices already reviewed — teaching the reader to skim — or hides
    one that has never been shown to anybody.
    """


@dataclass(frozen=True)
class ReviewedInvoice:
    """One KSeF number a person has been shown, and when it reached KSeF."""

    ksef_number: str
    received_on: date
    reviewed_at: datetime


@dataclass(frozen=True)
class ReviewLedger:
    entries: tuple[ReviewedInvoice, ...] = ()

    @property
    def reviewed(self) -> frozenset[str]:
        return frozenset(entry.ksef_number for entry in self.entries)

    def extended(self, entries: tuple[ReviewedInvoice, ...]) -> ReviewLedger:
        """Append what has just been shown, without recording the same number twice.

        A self-invoice reaches the same subject under two roles, and one loop
        over four subject types would then write it twice.
        """
        held = set(self.reviewed)
        added: list[ReviewedInvoice] = []
        for entry in entries:
            if entry.ksef_number in held:
                continue
            held.add(entry.ksef_number)
            added.append(entry)
        return replace(self, entries=(*self.entries, *added))


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _encode(ledger: ReviewLedger, *, nip: str, environment: KsefEnvironment) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "nip": nip,
        "environment": str(environment),
        "entries": [
            {
                "ksef_number": entry.ksef_number,
                "received_on": entry.received_on.isoformat(),
                "reviewed_at": entry.reviewed_at.isoformat(),
            }
            for entry in ledger.entries
        ],
    }


def _decode(document: dict[str, object]) -> ReviewLedger:
    found = document["schema_version"]
    if found != SCHEMA_VERSION:
        raise ReviewLedgerUnreadable(
            f"The review ledger is schema {found}, this build reads "
            f"{SCHEMA_VERSION}. Refusing to guess: a misread ledger either "
            f"repeats invoices already reviewed or hides one never shown."
        )
    entries: list[dict[str, object]] = document["entries"]  # type: ignore[assignment]
    return ReviewLedger(
        entries=tuple(
            ReviewedInvoice(
                ksef_number=str(entry["ksef_number"]),
                received_on=date.fromisoformat(str(entry["received_on"])),
                reviewed_at=datetime.fromisoformat(str(entry["reviewed_at"])),
            )
            for entry in entries
        )
    )


@dataclass(frozen=True)
class ReviewStore:
    """One subject's ledger of what has been shown, beside the archive it is not part of."""

    nip: str
    environment: KsefEnvironment
    root: Path | None = None

    @property
    def directory(self) -> Path:
        # The data root, and per subject and per environment like everything
        # beside it: one accounting office's clients sharing a directory is the
        # mixing vector D-034 names, and a ledger shared across environments
        # would report a production invoice as already reviewed because a test
        # run happened to show it.
        base = user_data_path(appname=SERVER_NAME) if self.root is None else self.root
        return base / SUBJECT_DIRECTORY / self.nip / str(self.environment)

    @property
    def path(self) -> Path:
        return self.directory / REVIEW_FILE

    def load(self) -> ReviewLedger:
        if not self.path.is_file():
            return ReviewLedger()
        return _decode(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, ledger: ReviewLedger) -> Path:
        self.directory.mkdir(mode=REVIEW_DIRECTORY_MODE, parents=True, exist_ok=True)
        document = _encode(ledger, nip=self.nip, environment=self.environment)
        # temp → rename (D-006). A half-written ledger read back as empty would
        # replay every invoice ever reviewed, and the interrupted write would
        # have destroyed the good one to do it.
        staging = self.path.with_suffix(STAGING_SUFFIX)
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, REVIEW_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        staging.chmod(REVIEW_FILE_MODE)
        os.replace(staging, self.path)
        return self.path


class ReviewOutcome(StrEnum):
    NOTHING_NEW = "nothing_new"
    REPORTED = "reported"
    SUMMARISED = "summarised"
    BUDGET_SPENT = "budget_spent"


def review_period(*, moment: datetime) -> Period:
    """Ninety days ending on the hour, so asking twice within the hour costs once."""
    ends = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return Period(date_from=ends - REVIEW_WINDOW, date_to=ends, date_type=REVIEW_DATE_TYPE)


def arrived_before_this_month(invoice: InvoiceMetadata, *, moment: datetime) -> bool:
    """Did this invoice get its KSeF number in a month earlier than the one running?

    A signal and nothing more. Whether that month is closed, and what follows if
    it is, is not something this tool knows or is entitled to say (ST-4).
    """
    received = invoice.ksef_number.assigned_on
    today = moment.astimezone(UTC).date()
    return (received.year, received.month) < (today.year, today.month)


@dataclass(frozen=True)
class DirectionReview:
    """One subject type's delta, already worded for the chat window."""

    question: Question
    outcome: ReviewOutcome
    message: str
    new_invoices: tuple[InvoiceMetadata, ...]
    new_count: int
    earlier_months: tuple[InvoiceMetadata, ...]
    gross_totals: tuple[CurrencyTotal, ...]
    complete: bool
    marked: bool
    queried_at: datetime | None
    from_cache: bool


@dataclass(frozen=True)
class InvoiceReview:
    nip: str
    environment: KsefEnvironment
    threshold: int
    period: Period
    ledger_path: str
    directions: tuple[DirectionReview, ...]


def _incompleteness(complete: bool) -> str:
    if complete:
        return ""
    # Worse here than in a plain listing: an incomplete window means the answer
    # "nothing new" can be wrong, and this tool exists to be trusted on exactly
    # that sentence.
    return (
        " KSeF nie oddał całego okna w jednej odpowiedzi, więc ta odpowiedź nie "
        "jest kompletem — brak nowych pozycji nie dowodzi, że ich nie ma."
    )


def _earlier_months_phrase(earlier: tuple[InvoiceMetadata, ...]) -> str:
    if not earlier:
        return ""
    days = sorted({invoice.ksef_number.assigned_on.isoformat() for invoice in earlier})
    return (
        f" {invoices_phrase(len(earlier))} z tego dostało numer KSeF przed "
        f"bieżącym miesiącem ({', '.join(days)}). Sprawdź, czy te okresy nie są "
        f"już rozliczone — o ujęciu decydujesz Ty, nie to narzędzie."
    )


def assess(
    *,
    question: Question,
    invoices: tuple[InvoiceMetadata, ...],
    reviewed: frozenset[str],
    complete: bool,
    moment: datetime,
    queried_at: datetime,
    from_cache: bool,
) -> DirectionReview:
    """Split the window into what has been shown before and what has not."""
    fresh = tuple(invoice for invoice in invoices if str(invoice.ksef_number) not in reviewed)
    count = len(fresh)
    earlier = tuple(
        invoice for invoice in fresh if arrived_before_this_month(invoice, moment=moment)
    )
    totals = gross_totals(fresh)
    if count == 0:
        return DirectionReview(
            question=question,
            outcome=ReviewOutcome.NOTHING_NEW,
            message=(
                f"Nic nowego od ostatniego przeglądu. Pytanie brzmiało — "
                f"{question.restated}.{_incompleteness(complete)}"
            ),
            new_invoices=(),
            new_count=0,
            earlier_months=(),
            gross_totals=(),
            complete=complete,
            marked=False,
            queried_at=queried_at,
            from_cache=from_cache,
        )
    if count > LISTING_THRESHOLD:
        # Nothing is marked as seen here on purpose: the rows were not written
        # out, so nobody saw them, and a ledger that says otherwise is worse than
        # no ledger. The next call reports them again.
        return DirectionReview(
            question=question,
            outcome=ReviewOutcome.SUMMARISED,
            message=(
                f"Nie wypisuję pozycji: {invoices_phrase(count)} nowych to więcej "
                f"niż próg {LISTING_THRESHOLD}. Brutto {describe_totals(totals)}. "
                f"Nie zapisuję ich jako pokazanych — nie zobaczyłaś ich pojedynczo, "
                f"więc kolejne wywołanie je powtórzy. Pytanie brzmiało — "
                f"{question.restated}.{_incompleteness(complete)}"
                f"{_earlier_months_phrase(earlier)}"
            ),
            new_invoices=(),
            new_count=count,
            earlier_months=earlier,
            gross_totals=totals,
            complete=complete,
            marked=False,
            queried_at=queried_at,
            from_cache=from_cache,
        )
    return DirectionReview(
        question=question,
        outcome=ReviewOutcome.REPORTED,
        message=(
            f"{invoices_phrase(count)} od ostatniego przeglądu, brutto "
            f"{describe_totals(totals)}. Zapisuję je jako pokazane, więc kolejne "
            f"wywołanie ich nie powtórzy.{_earlier_months_phrase(earlier)}"
            f"{_incompleteness(complete)}"
        ),
        new_invoices=fresh,
        new_count=count,
        earlier_months=earlier,
        gross_totals=totals,
        complete=complete,
        marked=True,
        queried_at=queried_at,
        from_cache=from_cache,
    )


def unasked(*, question: Question, refusal: AllowanceRefusal) -> DirectionReview:
    return DirectionReview(
        question=question,
        outcome=ReviewOutcome.BUDGET_SPENT,
        message=(
            f"Nie odpytałem KSeF-u o ten typ podmiotu, więc nie wiem, czy coś "
            f"doszło: {describe_refusal(refusal)} Pytanie brzmiało — {question.restated}."
        ),
        new_invoices=(),
        new_count=0,
        earlier_months=(),
        gross_totals=(),
        complete=False,
        marked=False,
        queried_at=None,
        from_cache=False,
    )


@dataclass(frozen=True)
class InvoiceReviewer:
    """Compares the window against the ledger, reports the difference, records it.

    Every subject type, the same loop the synchronisation and the listing run: a
    company is seller on one invoice and buyer on the next, and a delta covering
    one role would report a quiet quarter that was not (D-031 §5).
    """

    port: KsefPort
    cache: PeriodCache
    store: ReviewStore
    clock: Callable[[], datetime] = now_utc

    def run(self, *, nip: str, token: str) -> InvoiceReview:
        moment = self.clock()
        period = review_period(moment=moment)
        ledger = self.store.load()
        reviewed = ledger.reviewed
        reviews: list[DirectionReview] = []
        shown: list[ReviewedInvoice] = []
        with self.port.session(nip=nip, token=token) as session:
            reader = PeriodMetadataReader(
                cache=self.cache,
                budget=QueryBudget(limits=session.read_limits().rates, clock=self.clock),
            )
            for direction in SYNCHRONISED_DIRECTIONS:
                question = Question(
                    nip=nip,
                    environment=self.port.environment,
                    direction=direction,
                    period=period,
                )
                try:
                    answer = reader.read(session=session, period=period, direction=direction)
                except ALLOWANCE_REFUSALS as refusal:
                    # One exhausted subject type must not take the other three
                    # with it, and its own answer says the delta is unknown
                    # rather than empty. A real 429 belongs here too: losing the
                    # whole review over it would also lose what the ledger was
                    # about to be told (GH-95).
                    reviews.append(unasked(question=question, refusal=refusal))
                    continue
                review = assess(
                    question=question,
                    invoices=answer.page.invoices,
                    reviewed=reviewed,
                    complete=not (answer.page.has_more or answer.page.truncated),
                    moment=moment,
                    queried_at=answer.queried_at,
                    from_cache=answer.from_cache,
                )
                reviews.append(review)
                shown.extend(
                    ReviewedInvoice(
                        ksef_number=str(invoice.ksef_number),
                        received_on=invoice.ksef_number.assigned_on,
                        reviewed_at=moment,
                    )
                    for invoice in review.new_invoices
                )
        if shown:
            self.store.save(ledger.extended(tuple(shown)))
        return InvoiceReview(
            nip=nip,
            environment=self.port.environment,
            threshold=LISTING_THRESHOLD,
            period=period,
            ledger_path=str(self.store.path),
            directions=tuple(reviews),
        )
