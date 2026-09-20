"""Czy narzędzie powie „to jest nowe od ostatniego razu", i czy powie prawdę.

Tu leży jedyna dowiedziona przewaga nad darmową Aplikacją Podatnika [D-025]:
aplikacja pokazuje, co jest, ale nigdy — co doszło od ostatniego spojrzenia. Ta
różnica wykryła fakturę z 7 lipca w archiwum, które lipiec miało już zamknięty.

Sprawdzane są cztery niezmienniki. Znacznik „pokazane człowiekowi" jest trwały i
odrębny od indeksu deduplikacji [D-022], bo faktura zgłoszona jako nowa zwykle
nie ma jeszcze pliku w archiwum. Data otrzymania czytana jest z numeru KSeF, nie
z momentu pobrania. Powyżej progu [D-023] nic nie jest oznaczane jako pokazane,
bo nikt tego nie zobaczył. I nic tu nie rozstrzyga o ujęciu podatkowym — ST-4
zostawia tę decyzję człowiekowi.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import pytest

from conftest import an_allowance
from ksef_mcp import paths
from ksef_mcp.allowance import Allowance
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    DateType,
    InvoiceMetadata,
    KsefLimits,
    KsefNumber,
    KsefRateLimited,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
    SubjectRole,
)
from ksef_mcp.ksef_port.types import MAX_QUERY_WINDOW
from ksef_mcp.listing import LISTING_THRESHOLD, Question
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.period_cache import PeriodCache
from ksef_mcp.review import (
    REVIEW_WINDOW,
    InvoiceReviewer,
    ReviewedInvoice,
    ReviewLedger,
    ReviewLedgerUnreadable,
    ReviewOutcome,
    ReviewStore,
    SubjectRoleReview,
    arrived_before_this_month,
    assess,
    now_utc,
    review_period,
)
from synthetic import synthetic_credential, synthetic_metadata

NIP = "1234567890"

CREDENTIAL = synthetic_credential()

ASKED_AT = datetime(2026, 9, 14, 7, 41, 17, tzinfo=UTC)

ON_THE_HOUR = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)

GENEROUS: Final[OperationLimit] = OperationLimit(per_second=None, per_minute=None, per_hour=20)

EXHAUSTED: Final[OperationLimit] = OperationLimit(per_second=None, per_minute=None, per_hour=0)

# Ta faktura jest sednem zgłoszenia: numer nadany 7 lipca, wykryta we wrześniu.
IN_JULY = "1234567890-20260707-0100AB12CD77-56"

# Wyprowadzone z REVIEW_WINDOW, nie przepisane liczbą: przepisana data rozjeżdża
# się z oknem przy pierwszej zmianie pułapu i milczy o tym (GH-84).
WINDOW = Period(
    date_from=ON_THE_HOUR - REVIEW_WINDOW,
    date_to=ON_THE_HOUR,
    date_type=DateType.INVOICING,
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


def arrived(number: str) -> InvoiceMetadata:
    return replace(synthetic_metadata(1), ksef_number=KsefNumber(number))


def page_of(
    invoices: tuple[InvoiceMetadata, ...],
    *,
    has_more: bool = False,
    truncated: bool = False,
) -> MetadataPage:
    return MetadataPage(invoices=invoices, has_more=has_more, truncated=truncated, hwm_date=None)


def many(count: int) -> tuple[InvoiceMetadata, ...]:
    return tuple(synthetic_metadata(ordinal) for ordinal in range(1, count + 1))


@dataclass
class RecordingSession:
    page: MetadataPage
    allowance: OperationLimit = GENEROUS
    asked: list[SubjectRole] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    # What KSeF itself answers for a given subject type, so a test can put a
    # real 429 in the middle of the loop and ask what the earlier types kept.
    refusals: dict[SubjectRole, Exception] = field(default_factory=dict)

    def read_limits(self) -> KsefLimits:
        return limits(self.allowance)

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        self.asked.append(subject_role)
        self.offsets.append(page_offset)
        refusal = self.refusals.get(subject_role)
        if refusal is not None:
            raise refusal
        return self.page


@dataclass
class RecordingPort:
    session_object: RecordingSession
    environment: KsefEnvironment = KsefEnvironment.TEST

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[RecordingSession]:
        yield self.session_object


@pytest.fixture
def question() -> Question:
    return Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        subject_role=SubjectRole.BUYER,
        period=WINDOW,
    )


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    return ReviewStore(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)


@pytest.fixture
def cache(tmp_path: Path) -> PeriodCache:
    return PeriodCache(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path / "cache",
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def session() -> RecordingSession:
    return RecordingSession(page=page_of((arrived(IN_JULY),)))


@pytest.fixture
def protection(tmp_path: Path) -> Allowance:
    return an_allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path,
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def reviewer(
    session: RecordingSession,
    cache: PeriodCache,
    store: ReviewStore,
    protection: Allowance,
) -> InvoiceReviewer:
    return InvoiceReviewer(
        port=RecordingPort(session_object=session),
        cache=cache,
        store=store,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )


def test_the_window_reaches_a_full_quarter_back() -> None:
    # Przeoczona faktura miała trzy miesiące; okno trzydziestodniowe z listy
    # by jej nie objęło.
    assert review_period(moment=ASKED_AT).date_from == ON_THE_HOUR - REVIEW_WINDOW


def test_the_window_is_defined_from_the_ceiling_not_independently() -> None:
    # Strzeże definicji, nie zachowania. Dowody i limit wypadają w tym samym
    # miejscu, więc okno jest związane z pułapem, a nie przepisane liczbą —
    # inaczej rozjeżdżają się w ciszy (GH-84).
    assert REVIEW_WINDOW == MAX_QUERY_WINDOW


def test_the_window_still_reaches_the_invoice_the_study_found() -> None:
    # Sedno D-025: numer nadany 7 lipca, wykryty we wrześniu. Skrócenie okna do
    # pułapu nie może wypchnąć tej faktury poza zakres.
    assert review_period(moment=ASKED_AT).date_from < datetime(2026, 7, 7, tzinfo=UTC)


def test_the_window_ends_on_the_hour_so_a_repeat_costs_nothing() -> None:
    assert review_period(moment=ASKED_AT).date_to == ON_THE_HOUR


def test_the_window_is_dated_by_acceptance_in_ksef_not_by_the_sellers_date() -> None:
    # Faktura wystawiona w marcu, a przyjęta wczoraj, jest nowa dla czytającego;
    # okno po dacie wystawienia w ogóle by jej nie zawierało.
    assert review_period(moment=ASKED_AT).date_type is DateType.INVOICING


def test_a_number_assigned_in_an_earlier_month_is_flagged() -> None:
    assert arrived_before_this_month(arrived(IN_JULY), moment=ASKED_AT) is True


def test_a_number_assigned_this_month_is_not_flagged() -> None:
    assert arrived_before_this_month(synthetic_metadata(1), moment=ASKED_AT) is False


def test_an_empty_ledger_has_seen_nothing(store: ReviewStore) -> None:
    assert store.load().reviewed == frozenset()


def test_the_ledger_reads_back_what_was_written(store: ReviewStore) -> None:
    store.save(
        ReviewLedger(
            entries=(
                ReviewedInvoice(
                    ksef_number=IN_JULY, received_on=date(2026, 7, 7), reviewed_at=ASKED_AT
                ),
            )
        )
    )

    assert store.load().entries[0].received_on == date(2026, 7, 7)


def test_the_ledger_lands_in_the_data_root_not_the_cache_one(store: ReviewStore) -> None:
    # Utrata tego pliku nie kosztuje jednego zapytania, tylko zapis tego, co
    # człowiek już widział — a faktura z 7 lipca stałaby się cicho nowa (D-032).
    assert store.save(ReviewLedger()).name == "review.json"


def test_the_ledger_sits_beside_the_deduplication_index_not_inside_it(
    store: ReviewStore, tmp_path: Path
) -> None:
    assert store.path.parent == tmp_path / "subjects" / NIP / "test"


def test_without_a_root_the_ledger_follows_the_platform_data_directory() -> None:
    store = ReviewStore(nip=NIP, environment=KsefEnvironment.TEST)

    expected = paths.user_data_path(appname=SERVER_NAME) / "subjects" / NIP / "test"
    assert store.directory == expected


def test_the_ledger_file_is_private_to_its_owner(store: ReviewStore) -> None:
    assert store.save(ReviewLedger()).stat().st_mode & 0o777 == 0o600


def test_a_ledger_from_a_newer_build_is_refused_rather_than_guessed_at(
    store: ReviewStore,
) -> None:
    store.save(ReviewLedger())
    store.path.write_text('{"schema_version": 99, "entries": []}', encoding="utf-8")

    with pytest.raises(ReviewLedgerUnreadable, match="schemat 99"):
        store.load()


def test_an_update_extends_what_another_writer_recorded_meanwhile(
    store: ReviewStore,
) -> None:
    """GH-101: the snapshot a review ran against is minutes old by the time it writes."""
    stale = store.load()
    first = ReviewedInvoice(ksef_number=IN_JULY, received_on=date(2026, 7, 7), reviewed_at=ASKED_AT)
    second = replace(first, ksef_number=str(synthetic_metadata(1).ksef_number))
    store.save(stale.extended((first,)))

    store.updating(lambda held: held.extended((second,)))

    assert store.load().reviewed == {IN_JULY, second.ksef_number}


def test_the_ledger_does_not_record_the_same_number_twice() -> None:
    # Faktura własna dociera do tego samego podmiotu w dwóch rolach, a pętla
    # chodzi po czterech typach podmiotu.
    entry = ReviewedInvoice(ksef_number=IN_JULY, received_on=date(2026, 7, 7), reviewed_at=ASKED_AT)

    assert len(ReviewLedger().extended((entry, entry)).entries) == 1


def test_the_ledger_keeps_what_it_already_held() -> None:
    entry = ReviewedInvoice(ksef_number=IN_JULY, received_on=date(2026, 7, 7), reviewed_at=ASKED_AT)
    later = replace(entry, ksef_number=str(synthetic_metadata(1).ksef_number))

    assert ReviewLedger(entries=(entry,)).extended((later,)).reviewed == {
        IN_JULY,
        later.ksef_number,
    }


def assessed(
    question: Question,
    invoices: tuple[InvoiceMetadata, ...],
    *,
    reviewed: frozenset[str] = frozenset(),
    complete: bool = True,
    budget_bound: bool = False,
) -> SubjectRoleReview:
    return assess(
        question=question,
        invoices=invoices,
        reviewed=reviewed,
        complete=complete,
        budget_bound=budget_bound,
        moment=ASKED_AT,
        queried_at=ASKED_AT,
        from_cache=False,
    )


def test_an_invoice_never_shown_before_is_reported_as_new(question: Question) -> None:
    assert assessed(question, (arrived(IN_JULY),)).outcome is ReviewOutcome.REPORTED


def test_an_invoice_already_shown_is_not_reported_again(question: Question) -> None:
    assert (
        assessed(question, (arrived(IN_JULY),), reviewed=frozenset({IN_JULY})).outcome
        is ReviewOutcome.NOTHING_NEW
    )


def test_nothing_new_still_restates_the_question(question: Question) -> None:
    assert f"NIP {NIP}" in assessed(question, (), reviewed=frozenset()).message


def test_a_new_invoice_from_an_earlier_month_is_counted_apart(question: Question) -> None:
    reviewed = assessed(question, (arrived(IN_JULY), synthetic_metadata(1)))

    assert reviewed.earlier_months == (arrived(IN_JULY),)


def test_an_earlier_month_is_named_by_the_day_the_number_was_assigned(
    question: Question,
) -> None:
    assert "2026-07-07" in assessed(question, (arrived(IN_JULY),)).message


def test_an_earlier_month_is_a_question_to_the_reader_not_a_ruling(question: Question) -> None:
    # ST-4: narzędzie sygnalizuje, o ujęciu decyduje człowiek.
    assert "decydujesz Ty" in assessed(question, (arrived(IN_JULY),)).message


def test_an_invoice_from_this_month_raises_no_earlier_period_signal(
    question: Question,
) -> None:
    assert assessed(question, (synthetic_metadata(1),)).earlier_months == ()


def test_a_reported_invoice_is_marked_as_shown(question: Question) -> None:
    assert assessed(question, (arrived(IN_JULY),)).marked is True


def test_a_reported_answer_says_it_will_not_repeat_itself(question: Question) -> None:
    assert "nie powtórzy" in assessed(question, (arrived(IN_JULY),)).message


def test_a_reported_invoice_from_an_incomplete_window_is_not_marked_as_shown(
    question: Question,
) -> None:
    # Sedno #180: KSeF oddał tylko to, co się zmieściło. Zapis „pokazane" dla tej
    # części pozwala oknu przesunąć się ponad resztą i faktura nie wróci nigdy.
    assert assessed(question, (arrived(IN_JULY),), complete=False).marked is False


def test_a_reported_answer_from_an_incomplete_window_says_it_will_repeat_itself(
    question: Question,
) -> None:
    assert (
        "kolejne wywołanie je powtórzy"
        in assessed(question, (arrived(IN_JULY),), complete=False).message
    )


def test_a_delta_over_the_threshold_carries_no_rows(question: Question) -> None:
    assert assessed(question, many(LISTING_THRESHOLD + 1)).new_invoices == ()


def test_a_delta_over_the_threshold_still_says_how_many_there_were(question: Question) -> None:
    assert assessed(question, many(LISTING_THRESHOLD + 1)).new_count == LISTING_THRESHOLD + 1


def test_a_delta_over_the_threshold_is_not_marked_as_shown(question: Question) -> None:
    # Wierszy nie wypisano, więc nikt ich nie zobaczył — zapis, że widział,
    # byłby gorszy niż brak zapisu.
    assert assessed(question, many(LISTING_THRESHOLD + 1)).marked is False


def test_a_delta_over_the_threshold_says_it_will_repeat_itself(question: Question) -> None:
    assert "kolejne wywołanie je powtórzy" in assessed(question, many(51)).message


def test_a_delta_exactly_at_the_threshold_is_still_listed(question: Question) -> None:
    assert len(assessed(question, many(LISTING_THRESHOLD)).new_invoices) == LISTING_THRESHOLD


@pytest.mark.parametrize("invoices", [(), (arrived(IN_JULY),), many(51)])
def test_an_incomplete_window_says_that_nothing_new_proves_nothing(
    question: Question, invoices: tuple[InvoiceMetadata, ...]
) -> None:
    assert "nie jest kompletem" in assessed(question, invoices, complete=False).message


def test_the_answer_never_carries_an_invoice_body(question: Question) -> None:
    assert "<Faktura" not in assessed(question, (arrived(IN_JULY),)).message


def test_the_review_covers_every_subject_role(reviewer: InvoiceReviewer) -> None:
    reviewed = reviewer.run(nip=NIP, token=CREDENTIAL)

    assert [one.question.subject_role for one in reviewed.subject_roles] == [
        SubjectRole.SELLER,
        SubjectRole.BUYER,
        SubjectRole.THIRD_SUBJECT,
        SubjectRole.AUTHORIZED_SUBJECT,
    ]


def test_the_first_pass_reports_the_invoice_as_new(reviewer: InvoiceReviewer) -> None:
    reviewed = reviewer.run(nip=NIP, token=CREDENTIAL)

    assert reviewed.subject_roles[0].outcome is ReviewOutcome.REPORTED


def test_the_second_pass_reports_nothing_new(reviewer: InvoiceReviewer) -> None:
    reviewer.run(nip=NIP, token=CREDENTIAL)

    assert [one.outcome for one in reviewer.run(nip=NIP, token=CREDENTIAL).subject_roles] == [
        ReviewOutcome.NOTHING_NEW
    ] * 4


def test_what_was_shown_survives_a_new_process(
    reviewer: InvoiceReviewer, store: ReviewStore
) -> None:
    # Znacznik jest trwały: to jest cała różnica między funkcją a higieną [D-022].
    reviewer.run(nip=NIP, token=CREDENTIAL)

    assert store.load().reviewed == {IN_JULY}


def test_an_incomplete_window_leaves_the_ledger_untouched(
    reviewer: InvoiceReviewer, session: RecordingSession, store: ReviewStore
) -> None:
    # #180 od strony pliku: nic nie wchodzi do rejestru, dopóki okno nie jest
    # kompletem — inaczej pominięte faktury nie mają jak wrócić.
    session.page = page_of((arrived(IN_JULY),), has_more=True)

    reviewer.run(nip=NIP, token=CREDENTIAL)

    assert store.load().reviewed == frozenset()


def test_the_ledger_records_when_the_invoice_reached_ksef(
    reviewer: InvoiceReviewer, store: ReviewStore
) -> None:
    reviewer.run(nip=NIP, token=CREDENTIAL)

    assert store.load().entries[0].received_on == date(2026, 7, 7)


def test_the_review_points_at_the_ledger_it_keeps(
    reviewer: InvoiceReviewer, store: ReviewStore
) -> None:
    assert reviewer.run(nip=NIP, token=CREDENTIAL).ledger_path == str(store.path)


def test_the_review_states_the_window_it_asked_about(reviewer: InvoiceReviewer) -> None:
    assert reviewer.run(nip=NIP, token=CREDENTIAL).period == review_period(moment=ASKED_AT)


def test_the_review_names_the_subject_and_environment(reviewer: InvoiceReviewer) -> None:
    reviewed = reviewer.run(nip=NIP, token=CREDENTIAL)

    assert (reviewed.nip, reviewed.environment, reviewed.threshold) == (
        NIP,
        KsefEnvironment.TEST,
        LISTING_THRESHOLD,
    )


def test_asking_again_within_the_hour_spends_no_further_query(
    reviewer: InvoiceReviewer, session: RecordingSession
) -> None:
    reviewer.run(nip=NIP, token=CREDENTIAL)
    reviewer.run(nip=NIP, token=CREDENTIAL)

    assert len(session.asked) == 4


def test_an_unfetched_subject_role_reports_the_delta_as_unknown(
    cache: PeriodCache, store: ReviewStore, protection: Allowance
) -> None:
    reviewer = InvoiceReviewer(
        port=RecordingPort(
            session_object=RecordingSession(page=page_of((arrived(IN_JULY),)), allowance=EXHAUSTED)
        ),
        cache=cache,
        store=store,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )

    assert [one.outcome for one in reviewer.run(nip=NIP, token=CREDENTIAL).subject_roles] == [
        ReviewOutcome.BUDGET_SPENT
    ] * 4


def a_reviewer_meeting(
    refusal: Exception,
    *,
    cache: PeriodCache,
    store: ReviewStore,
    protection: Allowance,
) -> InvoiceReviewer:
    """A reviewer whose second subject type runs into `refusal` and whose first does not."""
    return InvoiceReviewer(
        port=RecordingPort(
            session_object=RecordingSession(
                page=page_of((arrived(IN_JULY),)),
                refusals={SubjectRole.BUYER: refusal},
            )
        ),
        cache=cache,
        store=store,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )


def test_a_rate_limit_on_one_subject_role_keeps_the_rest_of_the_review(
    cache: PeriodCache, store: ReviewStore, protection: Allowance
) -> None:
    # GH-95, the review side. Losing the whole pass to a 429 would also lose
    # what the ledger was about to be told about the types already read.
    reviewer = a_reviewer_meeting(
        KsefRateLimited("KSeF odmówił: 429.", retry_after=None),
        cache=cache,
        store=store,
        protection=protection,
    )

    assert [one.outcome for one in reviewer.run(nip=NIP, token=CREDENTIAL).subject_roles] == [
        ReviewOutcome.REPORTED,
        ReviewOutcome.BUDGET_SPENT,
        ReviewOutcome.REPORTED,
        ReviewOutcome.REPORTED,
    ]


def test_a_rate_limited_subject_role_says_it_does_not_know(
    cache: PeriodCache, store: ReviewStore, protection: Allowance
) -> None:
    reviewer = a_reviewer_meeting(
        KsefRateLimited("KSeF odmówił: 429.", retry_after=None),
        cache=cache,
        store=store,
        protection=protection,
    )

    assert (
        "nie wiem, czy coś doszło"
        in reviewer.run(nip=NIP, token=CREDENTIAL).subject_roles[1].message
    )


def test_an_unfetched_subject_role_does_not_claim_anything_was_shown(
    cache: PeriodCache, store: ReviewStore, protection: Allowance
) -> None:
    reviewer = InvoiceReviewer(
        port=RecordingPort(
            session_object=RecordingSession(page=page_of((arrived(IN_JULY),)), allowance=EXHAUSTED)
        ),
        cache=cache,
        store=store,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )
    reviewer.run(nip=NIP, token=CREDENTIAL)

    assert store.path.exists() is False


def test_an_unfetched_subject_role_says_it_does_not_know(
    cache: PeriodCache, store: ReviewStore, protection: Allowance
) -> None:
    reviewer = InvoiceReviewer(
        port=RecordingPort(
            session_object=RecordingSession(page=page_of((arrived(IN_JULY),)), allowance=EXHAUSTED)
        ),
        cache=cache,
        store=store,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )

    reviewed = reviewer.run(nip=NIP, token=CREDENTIAL)

    assert "nie wiem, czy coś doszło" in reviewed.subject_roles[0].message


def test_the_default_clock_reads_utc() -> None:
    assert now_utc().tzinfo is UTC
