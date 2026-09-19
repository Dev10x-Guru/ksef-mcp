"""Czy odpowiedź w oknie rozmowy da się ocenić bez otwierania katalogu.

Trzy rzeczy są tu sprawdzane i wszystkie trzy są niezmiennikami, nie
upiększeniami. Skrócenie listy jest powiedziane wprost (D-023). Pusty wynik jest
osobnym komunikatem i powtarza pytanie, więc „nic nie przyszło" da się odróżnić
od „zapytałeś o zły miesiąc". Metadane owszem, treść faktury nigdy (D-011).

Okno listy jest zamknięte i zaokrąglone do pełnej godziny — bez tego cache
okresów nie ma czego dopasować i każde pytanie kosztowałoby jedno z dwudziestu
zapytań na godzinę razy cztery typy podmiotu.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    DateType,
    InvoiceDirection,
    KsefAuthenticationFailed,
    KsefLimits,
    KsefRateLimited,
    KsefUnreachable,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
)
from ksef_mcp.listing import (
    LISTING_THRESHOLD,
    CurrencyTotal,
    InvoiceLister,
    ListingOutcome,
    Question,
    gross_totals,
    invoices_phrase,
    listing_period,
    now_utc,
    summarise,
)
from ksef_mcp.period_cache import PeriodCache
from synthetic import synthetic_metadata

NIP = "1234567890"

ASKED_AT = datetime(2026, 9, 14, 7, 41, 17, tzinfo=UTC)

ON_THE_HOUR = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)

GENEROUS: Final[OperationLimit] = OperationLimit(per_second=None, per_minute=None, per_hour=20)

EXHAUSTED: Final[OperationLimit] = OperationLimit(per_second=None, per_minute=None, per_hour=0)

SEPTEMBER = Period(
    date_from=datetime(2026, 9, 1, tzinfo=UTC),
    date_to=datetime(2026, 9, 30, tzinfo=UTC),
    date_type=DateType.ISSUE,
)


def limits(metadata: OperationLimit) -> KsefLimits:
    return KsefLimits(
        rates=RateLimits(
            metadata_queries=metadata,
            exports=GENEROUS,
            export_statuses=GENEROUS,
            invoice_downloads=GENEROUS,
        ),
        ceilings=SessionCeilings(
            max_invoice_megabytes=1,
            max_invoice_with_attachment_megabytes=3,
            max_invoices_per_session=10_000,
        ),
    )


def page_of(count: int, *, has_more: bool = False, truncated: bool = False) -> MetadataPage:
    return MetadataPage(
        invoices=tuple(synthetic_metadata(ordinal) for ordinal in range(1, count + 1)),
        has_more=has_more,
        truncated=truncated,
        hwm_date=None,
    )


@dataclass
class RecordingSession:
    """Liczy, ile z dwudziestu zapytań na godzinę naprawdę poszło do KSeF-u."""

    page: MetadataPage
    allowance: OperationLimit = GENEROUS
    asked: list[InvoiceDirection] = field(default_factory=list)
    # What KSeF itself answers for a given subject type. Keyed, because the
    # whole question of GH-95 is what happens to the types asked *before* the
    # one that is refused.
    refusals: dict[InvoiceDirection, Exception] = field(default_factory=dict)

    def read_limits(self) -> KsefLimits:
        return limits(self.allowance)

    def query_metadata(self, *, period: Period, direction: InvoiceDirection) -> MetadataPage:
        self.asked.append(direction)
        refusal = self.refusals.get(direction)
        if refusal is not None:
            raise refusal
        return self.page


@dataclass
class RecordingPort:
    session_object: RecordingSession
    environment: KsefEnvironment = KsefEnvironment.TEST
    credentials: list[tuple[str, str]] = field(default_factory=list)

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[RecordingSession]:
        self.credentials.append((nip, token))
        yield self.session_object


@pytest.fixture
def question() -> Question:
    return Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        direction=InvoiceDirection.BUYER,
        period=SEPTEMBER,
    )


@pytest.fixture
def cache(tmp_path: Path) -> PeriodCache:
    return PeriodCache(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path,
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def session() -> RecordingSession:
    return RecordingSession(page=page_of(2))


@pytest.fixture
def lister(session: RecordingSession, cache: PeriodCache) -> InvoiceLister:
    return InvoiceLister(
        port=RecordingPort(session_object=session), cache=cache, clock=lambda: ASKED_AT
    )


def test_the_window_ends_on_the_hour_so_the_same_question_is_answered_from_disk() -> None:
    assert listing_period(moment=ASKED_AT).date_to == ON_THE_HOUR


def test_the_window_reaches_thirty_days_back() -> None:
    assert listing_period(moment=ASKED_AT).date_from == ON_THE_HOUR - timedelta(days=30)


def test_the_window_is_dated_by_the_issue_date_the_taxpayer_asks_in() -> None:
    assert listing_period(moment=ASKED_AT).date_type is DateType.ISSUE


def test_the_window_has_both_ends_so_the_period_cache_can_keep_it() -> None:
    # Okno datowane jak synchronizacja jest z definicji niecacheowalne, więc
    # każde powtórzone pytanie kosztowałoby jedno zapytanie na typ podmiotu.
    assert listing_period(moment=ASKED_AT).date_type is not DateType.PERMANENT_STORAGE


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "1 faktura"),
        (2, "2 faktury"),
        (3, "3 faktury"),
        (4, "4 faktury"),
        (5, "5 faktur"),
        (12, "12 faktur"),
        (13, "13 faktur"),
        (14, "14 faktur"),
        (22, "22 faktury"),
        (25, "25 faktur"),
        (112, "112 faktur"),
        (812, "812 faktur"),
    ],
)
def test_the_count_is_spelled_in_correct_polish(count: int, expected: str) -> None:
    assert invoices_phrase(count) == expected


def test_the_gross_total_is_summed_per_currency() -> None:
    invoices = (
        synthetic_metadata(1),
        replace(synthetic_metadata(2), currency="EUR", gross_amount=Decimal("100.00")),
        synthetic_metadata(3),
    )

    assert gross_totals(invoices) == (
        CurrencyTotal(currency="EUR", gross=Decimal("100.00")),
        CurrencyTotal(currency="PLN", gross=Decimal("2460.00")),
    )


def test_a_total_reads_as_an_amount_with_its_currency() -> None:
    assert str(CurrencyTotal(currency="PLN", gross=Decimal("1230.00"))) == "1230.00 PLN"


def test_a_short_list_is_listed_in_full(question: Question) -> None:
    listed = summarise(question=question, page=page_of(3), queried_at=ASKED_AT)

    assert (listed.outcome, len(listed.invoices)) == (ListingOutcome.LISTED, 3)


def test_a_list_exactly_at_the_threshold_is_still_listed(question: Question) -> None:
    listed = summarise(question=question, page=page_of(LISTING_THRESHOLD), queried_at=ASKED_AT)

    assert len(listed.invoices) == LISTING_THRESHOLD


def test_a_list_over_the_threshold_carries_no_rows_at_all(question: Question) -> None:
    # D-023: powyżej progu narzędzie zwraca liczbę i sumę brutto, nigdy listę.
    listed = summarise(question=question, page=page_of(LISTING_THRESHOLD + 1), queried_at=ASKED_AT)

    assert (listed.outcome, listed.invoices) == (ListingOutcome.SUMMARISED, ())


def test_a_list_over_the_threshold_still_reports_how_many_there_were(question: Question) -> None:
    listed = summarise(question=question, page=page_of(51), queried_at=ASKED_AT)

    assert listed.invoice_count == 51


def test_a_shortened_answer_says_the_count_out_loud(question: Question) -> None:
    # Cicha obcinka zabija zaufanie natychmiast — liczba musi paść w zdaniu.
    listed = summarise(question=question, page=page_of(51), queried_at=ASKED_AT)

    assert "51 faktur" in listed.message


def test_a_shortened_answer_names_the_threshold_it_hit(question: Question) -> None:
    listed = summarise(question=question, page=page_of(51), queried_at=ASKED_AT)

    assert "próg 50" in listed.message


def test_a_shortened_answer_carries_the_gross_total(question: Question) -> None:
    listed = summarise(question=question, page=page_of(51), queried_at=ASKED_AT)

    assert listed.gross_totals == (CurrencyTotal(currency="PLN", gross=Decimal("62730.00")),)


def test_an_empty_window_is_its_own_answer(question: Question) -> None:
    listed = summarise(question=question, page=page_of(0), queried_at=ASKED_AT)

    assert listed.outcome is ListingOutcome.EMPTY


@pytest.mark.parametrize(
    "expected",
    ["NIP 1234567890", "środowisko test", "nabywca", "2026-09-01", "2026-09-30", "wystawienia"],
)
def test_an_empty_answer_restates_exactly_what_was_asked(question: Question, expected: str) -> None:
    listed = summarise(question=question, page=page_of(0), queried_at=ASKED_AT)

    assert expected in listed.message


def test_a_restated_question_names_both_ends() -> None:
    # Od GH-84 okno bez końca nie istnieje jako typ, więc nie ma już czego
    # opisywać jako „bez końca" — pytanie zawsze podaje oba końce.
    asked = Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        direction=InvoiceDirection.SELLER,
        period=Period(
            date_from=datetime(2026, 9, 1, tzinfo=UTC),
            date_to=datetime(2026, 9, 14, tzinfo=UTC),
            date_type=DateType.PERMANENT_STORAGE,
        ),
    )

    assert "od 2026-09-01T00:00:00+00:00 do 2026-09-14T00:00:00+00:00" in asked.restated


def test_a_page_ksef_shortened_is_reported_as_incomplete(question: Question) -> None:
    listed = summarise(question=question, page=page_of(3, has_more=True), queried_at=ASKED_AT)

    assert listed.complete is False


def test_a_page_ksef_shortened_says_so_in_the_message(question: Question) -> None:
    listed = summarise(question=question, page=page_of(3, truncated=True), queried_at=ASKED_AT)

    assert "to nie jest komplet" in listed.message


def test_an_empty_page_ksef_shortened_says_so_too(question: Question) -> None:
    listed = summarise(question=question, page=page_of(0, has_more=True), queried_at=ASKED_AT)

    assert "to nie jest komplet" in listed.message


def test_a_full_page_is_reported_as_complete(question: Question) -> None:
    listed = summarise(question=question, page=page_of(3), queried_at=ASKED_AT)

    assert listed.complete is True


def test_the_answer_never_carries_an_invoice_body(question: Question) -> None:
    # FA(2)/FA(3) to niezaufane wejście z danymi osobowymi kontrahenta (D-011).
    listed = summarise(question=question, page=page_of(3), queried_at=ASKED_AT)

    assert "<Faktura" not in listed.message


def test_the_listing_covers_every_subject_type(lister: InvoiceLister) -> None:
    # Ta sama pętla co synchronizacja: firma bywa sprzedawcą na jednej fakturze
    # i nabywcą na następnej (D-031 §5).
    listed = lister.run(nip=NIP, token="tajny-token")

    assert [one.question.direction for one in listed.directions] == [
        InvoiceDirection.SELLER,
        InvoiceDirection.BUYER,
        InvoiceDirection.THIRD_SUBJECT,
        InvoiceDirection.AUTHORIZED_SUBJECT,
    ]


def test_the_listing_names_the_environment_it_spoke_to(lister: InvoiceLister) -> None:
    assert lister.run(nip=NIP, token="tajny-token").environment is KsefEnvironment.TEST


def test_the_listing_names_the_threshold_it_applied(lister: InvoiceLister) -> None:
    assert lister.run(nip=NIP, token="tajny-token").threshold == LISTING_THRESHOLD


def test_the_listing_states_the_window_it_asked_about(lister: InvoiceLister) -> None:
    assert lister.run(nip=NIP, token="tajny-token").period == listing_period(moment=ASKED_AT)


def test_the_first_pass_pays_one_query_per_subject_type(
    lister: InvoiceLister, session: RecordingSession
) -> None:
    lister.run(nip=NIP, token="tajny-token")

    assert len(session.asked) == 4


def test_asking_again_within_the_hour_costs_nothing(
    lister: InvoiceLister, session: RecordingSession
) -> None:
    lister.run(nip=NIP, token="tajny-token")
    lister.run(nip=NIP, token="tajny-token")

    assert len(session.asked) == 4


def test_the_second_answer_says_it_came_from_disk(lister: InvoiceLister) -> None:
    lister.run(nip=NIP, token="tajny-token")

    assert [one.from_cache for one in lister.run(nip=NIP, token="tajny-token").directions] == [
        True,
        True,
        True,
        True,
    ]


def test_a_spent_allowance_does_not_take_the_other_subject_types_down(cache: PeriodCache) -> None:
    lister = InvoiceLister(
        port=RecordingPort(session_object=RecordingSession(page=page_of(2), allowance=EXHAUSTED)),
        cache=cache,
        clock=lambda: ASKED_AT,
    )

    assert [one.outcome for one in lister.run(nip=NIP, token="tajny-token").directions] == [
        ListingOutcome.BUDGET_SPENT
    ] * 4


def test_a_spent_allowance_explains_itself_and_repeats_the_question(cache: PeriodCache) -> None:
    lister = InvoiceLister(
        port=RecordingPort(session_object=RecordingSession(page=page_of(2), allowance=EXHAUSTED)),
        cache=cache,
        clock=lambda: ASKED_AT,
    )

    assert "NIP 1234567890" in lister.run(nip=NIP, token="tajny-token").directions[0].message


def test_a_spent_allowance_reports_no_moment_of_asking(cache: PeriodCache) -> None:
    lister = InvoiceLister(
        port=RecordingPort(session_object=RecordingSession(page=page_of(2), allowance=EXHAUSTED)),
        cache=cache,
        clock=lambda: ASKED_AT,
    )

    assert lister.run(nip=NIP, token="tajny-token").directions[0].queried_at is None


def a_lister_meeting(refusal: Exception, *, cache: PeriodCache) -> InvoiceLister:
    """A lister whose second subject type runs into `refusal` and whose first does not."""
    return InvoiceLister(
        port=RecordingPort(
            session_object=RecordingSession(
                page=page_of(2),
                refusals={InvoiceDirection.BUYER: refusal},
            )
        ),
        cache=cache,
        clock=lambda: ASKED_AT,
    )


def test_a_rate_limit_on_one_subject_type_keeps_the_answers_already_paid_for(
    cache: PeriodCache,
) -> None:
    # GH-95. A real 429 is a sibling of the local refusal rather than a
    # subclass, so it used to sail through the `except` and out of `run` —
    # taking with it the seller's answer, which had already cost one of twenty
    # metadata queries an hour.
    lister = a_lister_meeting(KsefRateLimited("KSeF odmówił: 429.", retry_after=None), cache=cache)

    assert [one.outcome for one in lister.run(nip=NIP, token="tajny-token").directions] == [
        ListingOutcome.LISTED,
        ListingOutcome.BUDGET_SPENT,
        ListingOutcome.LISTED,
        ListingOutcome.LISTED,
    ]


def test_a_rate_limit_passes_on_the_wait_ksef_itself_asked_for(cache: PeriodCache) -> None:
    # KSeF's own number, never one of ours: a guessed pause is the pattern the
    # Ministry answers with a lengthening block (D-017).
    lister = a_lister_meeting(KsefRateLimited("KSeF odmówił: 429.", retry_after=90), cache=cache)

    assert "odczekanie 90 s" in lister.run(nip=NIP, token="tajny-token").directions[1].message


def test_a_rate_limit_without_a_wait_promises_nothing_about_waiting(cache: PeriodCache) -> None:
    lister = a_lister_meeting(KsefRateLimited("KSeF odmówił: 429.", retry_after=None), cache=cache)

    assert "odczekanie" not in lister.run(nip=NIP, token="tajny-token").directions[1].message


@pytest.mark.parametrize(
    "failure",
    [
        KsefUnreachable("Brak odpowiedzi z KSeF-u."),
        KsefAuthenticationFailed("KSeF nie uznał tego tokenu."),
    ],
    ids=["unreachable", "rejected-credential"],
)
def test_a_failure_that_is_not_about_the_allowance_is_not_reported_as_one(
    failure: Exception, cache: PeriodCache
) -> None:
    # The `except` names two refusals and only two. Widening it to
    # `KsefPortError` would answer a dead network or a rejected credential with
    # a cheerful partial listing, which is worse than no listing at all.
    lister = a_lister_meeting(failure, cache=cache)

    with pytest.raises(type(failure)):
        lister.run(nip=NIP, token="tajny-token")


def test_the_default_clock_reads_utc() -> None:
    assert now_utc().tzinfo is UTC
