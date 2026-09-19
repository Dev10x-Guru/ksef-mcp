"""Asking twice, paying once — and what the two roots cost when they are lost.

The assertions here are about placement and arithmetic. Placement: the cache
root, never the data root, so a disk cleaner honouring "safe to delete at any
moment" costs one query rather than a full resynchronisation (D-032).
Arithmetic: the second question about the same period and the same subject type
spends zero of the twenty metadata queries an hour (D-021).
"""

import json
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from platformdirs import user_cache_path

from ksef_mcp import paths
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    DateType,
    InvoiceDirection,
    MetadataPage,
    OperationLimit,
    Period,
    QueryBudget,
    RateLimits,
)
from ksef_mcp.ksef_port.budget import Operation
from ksef_mcp.ksef_port.errors import KsefRequestRejected
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.period_cache import (
    SCHEMA_VERSION,
    CachedPeriod,
    PeriodCache,
    PeriodMetadataReader,
    cache_key,
    cache_root,
    is_cacheable,
    now_utc,
)
from ksef_mcp.sync_store import DirectionState, SyncState, SyncStore
from synthetic import synthetic_metadata

NIP = "1234567890"

SEPTEMBER = Period(
    date_from=datetime(2026, 9, 1, tzinfo=UTC),
    date_to=datetime(2026, 9, 30, tzinfo=UTC),
    date_type=DateType.ISSUE,
)

AUGUST = Period(
    date_from=datetime(2026, 8, 1, tzinfo=UTC),
    date_to=datetime(2026, 8, 31, tzinfo=UTC),
    date_type=DateType.ISSUE,
)

# Okno synchronizacji — od GH-84 z oboma końcami. Niecacheowalne nie dlatego, że
# nie ma końca, tylko dlatego, że MF zatrzymuje paczkę tam, gdzie zechce.
SYNCHRONISATION = Period.for_synchronisation(
    since=datetime(2026, 9, 1, tzinfo=UTC),
    now=datetime(2026, 9, 14, 6, 0, tzinfo=UTC),
)

ASKED_AT = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)

GENEROUS = OperationLimit(per_second=None, per_minute=None, per_hour=20)


@dataclass
class CountingSession:
    """A session that says how many of the twenty an hour were actually spent."""

    page: MetadataPage
    asked: list[tuple[Period, InvoiceDirection]] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    # Pages beyond the first, keyed by their zero-based number, so the
    # completing loop meets a real continuation rather than the same page twice.
    further: dict[int, MetadataPage] = field(default_factory=dict)

    def query_metadata(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
        page_offset: int = 0,
    ) -> MetadataPage:
        self.asked.append((period, direction))
        self.offsets.append(page_offset)
        return self.further.get(page_offset, self.page)


@pytest.fixture
def page() -> MetadataPage:
    # Complete, because since GH-182 only a complete window is written to disk:
    # nothing in this root expires, so remembering a stump would make it the
    # answer forever.
    return MetadataPage(
        invoices=(synthetic_metadata(1), synthetic_metadata(2, seller_name=None)),
        has_more=False,
        truncated=False,
        hwm_date=datetime(2026, 9, 30, 23, 59, tzinfo=UTC),
    )


@pytest.fixture
def opening() -> MetadataPage:
    """A first page KSeF says has a continuation behind it."""
    return MetadataPage(
        invoices=(synthetic_metadata(1),),
        has_more=True,
        truncated=False,
        hwm_date=datetime(2026, 9, 30, 23, 59, tzinfo=UTC),
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
def session(page: MetadataPage) -> CountingSession:
    return CountingSession(page=page)


@pytest.fixture
def budget() -> QueryBudget:
    return QueryBudget(
        limits=RateLimits(
            metadata_queries=GENEROUS,
            exports=GENEROUS,
            export_statuses=GENEROUS,
            invoice_downloads=GENEROUS,
        ),
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def reader(cache: PeriodCache, budget: QueryBudget) -> PeriodMetadataReader:
    return PeriodMetadataReader(cache=cache, budget=budget)


@pytest.fixture
def remembered(cache: PeriodCache, page: MetadataPage) -> CachedPeriod | None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    return cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER)


@pytest.fixture
def entry_path(cache: PeriodCache) -> Path:
    return cache.path_for(period=SEPTEMBER, direction=InvoiceDirection.BUYER)


def _corrupt(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_period_never_asked_about_is_a_miss(cache: PeriodCache) -> None:
    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_a_remembered_period_comes_back_with_its_invoices(
    remembered: CachedPeriod | None, page: MetadataPage
) -> None:
    assert remembered is not None
    assert remembered.page == page


def test_a_remembered_period_comes_back_with_the_window_it_answered(
    remembered: CachedPeriod | None,
) -> None:
    assert remembered is not None
    assert remembered.period == SEPTEMBER


def test_a_remembered_period_comes_back_with_the_subject_type_it_answered(
    remembered: CachedPeriod | None,
) -> None:
    assert remembered is not None
    assert remembered.direction is InvoiceDirection.BUYER


def test_the_marker_of_the_last_successful_query_survives_the_write(
    remembered: CachedPeriod | None,
) -> None:
    assert remembered is not None
    assert remembered.queried_at == ASKED_AT


def test_the_entry_outlives_the_object_that_wrote_it(
    cache: PeriodCache, page: MetadataPage, tmp_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    restarted = PeriodCache(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)

    assert restarted.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is not None


def test_the_seller_type_does_not_answer_the_buyer_question(
    cache: PeriodCache, page: MetadataPage
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.SELLER, page=page)

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


@pytest.mark.parametrize(
    "other",
    [
        AUGUST,
        Period(
            date_from=SEPTEMBER.date_from,
            date_to=datetime(2026, 9, 29, tzinfo=UTC),
            date_type=DateType.ISSUE,
        ),
        Period(
            date_from=SEPTEMBER.date_from,
            date_to=SEPTEMBER.date_to,
            date_type=DateType.INVOICING,
        ),
    ],
)
def test_a_different_window_is_a_different_question(
    cache: PeriodCache, page: MetadataPage, other: Period
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)

    assert cache.remembered(period=other, direction=InvoiceDirection.BUYER) is None


def test_another_subject_reads_none_of_this_ones_periods(
    cache: PeriodCache, page: MetadataPage, tmp_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    other = PeriodCache(nip="9876543210", environment=KsefEnvironment.TEST, root=tmp_path)

    assert other.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_the_demonstration_environment_reads_none_of_the_test_answers(
    cache: PeriodCache, page: MetadataPage, tmp_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    demonstration = PeriodCache(nip=NIP, environment=KsefEnvironment.DEMO, root=tmp_path)

    assert demonstration.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_the_key_names_the_subject_type_it_belongs_to() -> None:
    assert cache_key(period=SEPTEMBER, direction=InvoiceDirection.BUYER).startswith("buyer-")


def test_a_synchronisation_window_still_has_a_key_to_look_up_by() -> None:
    assert cache_key(period=SYNCHRONISATION, direction=InvoiceDirection.BUYER).startswith("buyer-")


def test_two_windows_dated_differently_are_different_questions() -> None:
    assert cache_key(period=SYNCHRONISATION, direction=InvoiceDirection.BUYER) != cache_key(
        period=SEPTEMBER, direction=InvoiceDirection.BUYER
    )


@pytest.mark.parametrize(
    ("period", "expected"),
    [(SEPTEMBER, True), (SYNCHRONISATION, False)],
)
def test_only_a_settled_period_is_worth_remembering(period: Period, expected: bool) -> None:
    assert is_cacheable(period) is expected


def test_a_synchronisation_window_is_refused_although_it_states_both_ends() -> None:
    """GH-84: the refusal follows the date type, not a missing end."""
    assert is_cacheable(SYNCHRONISATION) is False


def test_a_synchronisation_window_leaves_nothing_behind(
    cache: PeriodCache, page: MetadataPage
) -> None:
    cache.remember(period=SYNCHRONISATION, direction=InvoiceDirection.BUYER, page=page)

    assert cache.remembered(period=SYNCHRONISATION, direction=InvoiceDirection.BUYER) is None


def test_a_synchronisation_window_is_still_stamped_for_the_caller_to_report(
    cache: PeriodCache, page: MetadataPage
) -> None:
    stamped = cache.remember(period=SYNCHRONISATION, direction=InvoiceDirection.BUYER, page=page)

    assert stamped.queried_at == ASKED_AT


def test_an_entry_written_before_the_ceiling_is_a_miss_not_a_crash(
    cache: PeriodCache, page: MetadataPage
) -> None:
    """GH-84: a stored window with no end can no longer become a Period."""
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    path = cache.path_for(period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["period"]["date_to"] = None
    path.write_text(json.dumps(document), encoding="utf-8")

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_the_default_clock_stamps_a_moment_with_a_timezone(
    tmp_path: Path, page: MetadataPage
) -> None:
    unclocked = PeriodCache(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)

    stamped = unclocked.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)

    assert stamped.queried_at.tzinfo is not None


def test_the_module_clock_reads_utc() -> None:
    assert now_utc().tzinfo is UTC


def test_the_cache_lives_in_the_cache_root() -> None:
    placed = PeriodCache(nip=NIP, environment=KsefEnvironment.TEST)

    assert placed.directory.is_relative_to(paths.user_cache_path(appname=SERVER_NAME))


def test_the_cache_root_is_the_platformdirs_one_for_this_server() -> None:
    assert cache_root() == user_cache_path(appname=SERVER_NAME)


def test_the_cache_root_is_not_the_data_root() -> None:
    placed = PeriodCache(nip=NIP, environment=KsefEnvironment.TEST)

    assert not placed.directory.is_relative_to(paths.user_data_path(appname=SERVER_NAME))


def test_the_entry_is_readable_only_by_its_owner(
    cache: PeriodCache, page: MetadataPage, entry_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)

    assert stat.S_IMODE(entry_path.stat().st_mode) == 0o600


def test_the_directory_is_readable_only_by_its_owner(
    cache: PeriodCache, page: MetadataPage
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)

    assert stat.S_IMODE(cache.directory.stat().st_mode) == 0o700


def test_the_staging_file_does_not_outlive_the_write(
    cache: PeriodCache, page: MetadataPage
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)

    assert list(cache.directory.glob("*.tmp")) == []


def test_an_answer_written_twice_leaves_one_entry(cache: PeriodCache, page: MetadataPage) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)

    assert len(list(cache.directory.glob("*.json"))) == 1


def test_the_stored_amounts_are_not_binary_fractions(
    cache: PeriodCache, page: MetadataPage, entry_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    stored = json.loads(entry_path.read_text(encoding="utf-8"))

    assert stored["page"]["invoices"][0]["gross_amount"] == "1230.00"


def test_a_page_without_a_high_water_mark_round_trips(cache: PeriodCache) -> None:
    unmarked = MetadataPage(invoices=(), has_more=False, truncated=False, hwm_date=None)
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=unmarked)

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) == CachedPeriod(
        period=SEPTEMBER,
        direction=InvoiceDirection.BUYER,
        page=unmarked,
        queried_at=ASKED_AT,
    )


@pytest.mark.parametrize(
    "damage",
    [
        {"schema_version": SCHEMA_VERSION + 1},
        {"schema_version": SCHEMA_VERSION},
        {"schema_version": SCHEMA_VERSION, "period": None},
    ],
)
def test_a_damaged_entry_is_a_miss_not_a_failure(
    cache: PeriodCache, page: MetadataPage, entry_path: Path, damage: dict[str, object]
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    _corrupt(entry_path, damage)

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_an_entry_that_is_not_json_at_all_is_a_miss(
    cache: PeriodCache, page: MetadataPage, entry_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    entry_path.write_text("nie-json", encoding="utf-8")

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_an_entry_naming_something_that_is_not_a_ksef_number_is_a_miss(
    cache: PeriodCache, page: MetadataPage, entry_path: Path
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    document = json.loads(entry_path.read_text(encoding="utf-8"))
    document["page"]["invoices"][0]["ksef_number"] = "FV/2026/09/001"
    _corrupt(entry_path, document)

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_the_first_question_about_a_period_reaches_ksef(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert session.asked == [(SEPTEMBER, InvoiceDirection.BUYER)]


def test_the_second_question_about_the_same_period_reaches_nobody(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert len(session.asked) == 1


def test_the_second_question_about_the_same_period_costs_zero_of_the_allowance(
    reader: PeriodMetadataReader, session: CountingSession, budget: QueryBudget
) -> None:
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert budget.remaining(Operation.METADATA_QUERY) == 19


def test_the_second_question_gives_back_the_same_invoices(
    reader: PeriodMetadataReader, session: CountingSession, page: MetadataPage
) -> None:
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    again = reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert again.page == page


def test_the_second_question_says_it_was_answered_from_disk(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    again = reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert again.from_cache is True


def test_the_first_question_says_it_was_paid_for(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    first = reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert first.from_cache is False


def test_the_answer_carries_the_moment_the_period_was_last_paid_for(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    first = reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert first.queried_at == ASKED_AT


def test_the_same_period_for_another_subject_type_is_paid_for_separately(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.SELLER)

    assert len(session.asked) == 2


def test_a_synchronisation_window_is_asked_about_every_time(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    reader.read(session=session, period=SYNCHRONISATION, direction=InvoiceDirection.BUYER)
    reader.read(session=session, period=SYNCHRONISATION, direction=InvoiceDirection.BUYER)

    assert len(session.asked) == 2


def test_a_spent_allowance_refuses_the_first_question(
    cache: PeriodCache, session: CountingSession
) -> None:
    spent = QueryBudget(
        limits=RateLimits(
            metadata_queries=OperationLimit(per_second=None, per_minute=None, per_hour=0),
            exports=GENEROUS,
            export_statuses=GENEROUS,
            invoice_downloads=GENEROUS,
        ),
        clock=lambda: ASKED_AT,
    )
    starved = PeriodMetadataReader(cache=cache, budget=spent)

    with pytest.raises(KsefRequestRejected):
        starved.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)


def test_a_spent_allowance_still_answers_a_period_already_on_disk(
    cache: PeriodCache, session: CountingSession, page: MetadataPage
) -> None:
    cache.remember(period=SEPTEMBER, direction=InvoiceDirection.BUYER, page=page)
    spent = QueryBudget(
        limits=RateLimits(
            metadata_queries=OperationLimit(per_second=None, per_minute=None, per_hour=0),
            exports=GENEROUS,
            export_statuses=GENEROUS,
            invoice_downloads=GENEROUS,
        ),
        clock=lambda: ASKED_AT,
    )
    starved = PeriodMetadataReader(cache=cache, budget=spent)

    assert (
        starved.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER).page
        == page
    )


def test_deleting_the_cache_root_costs_one_query_and_no_continuation_point(
    tmp_path: Path, page: MetadataPage, session: CountingSession, budget: QueryBudget
) -> None:
    """The acceptance criterion of #39, written as the two roots being two roots."""
    cache_directory = tmp_path / "cache"
    data_directory = tmp_path / "data"
    cached = PeriodCache(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=cache_directory,
        clock=lambda: ASKED_AT,
    )
    store = SyncStore(nip=NIP, environment=KsefEnvironment.TEST, root=data_directory)
    store.save(
        SyncState(
            directions={
                InvoiceDirection.BUYER: DirectionState(reached=ASKED_AT - timedelta(days=1))
            }
        )
    )
    PeriodMetadataReader(cache=cached, budget=budget).read(
        session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER
    )

    _erase(cache_directory)

    assert store.load().directions[InvoiceDirection.BUYER].reached == ASKED_AT - timedelta(days=1)


def test_deleting_the_cache_root_makes_the_period_cost_one_query_again(
    tmp_path: Path, session: CountingSession, budget: QueryBudget
) -> None:
    cache_directory = tmp_path / "cache"
    cached = PeriodCache(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=cache_directory,
        clock=lambda: ASKED_AT,
    )
    reader = PeriodMetadataReader(cache=cached, budget=budget)
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    _erase(cache_directory)
    reader.read(session=session, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert len(session.asked) == 2


@pytest.fixture
def closing() -> MetadataPage:
    """What is behind the `has_more`, and the end of the window."""
    return MetadataPage(
        invoices=(synthetic_metadata(2),),
        has_more=False,
        truncated=False,
        hwm_date=datetime(2026, 9, 30, 23, 59, tzinfo=UTC),
        page_offset=1,
    )


@pytest.fixture
def paging(opening: MetadataPage, closing: MetadataPage) -> CountingSession:
    return CountingSession(page=opening, further={1: closing})


@pytest.fixture
def scarce(cache: PeriodCache) -> PeriodMetadataReader:
    """A reader with four metadata queries left: one page, then the reserve."""
    return PeriodMetadataReader(
        cache=cache,
        budget=QueryBudget(
            limits=RateLimits(
                metadata_queries=OperationLimit(per_second=None, per_minute=None, per_hour=4),
                exports=GENEROUS,
                export_statuses=GENEROUS,
                invoice_downloads=GENEROUS,
            ),
            clock=lambda: ASKED_AT,
        ),
    )


@pytest.fixture
def uncounted(cache: PeriodCache) -> PeriodMetadataReader:
    """A reader whose counter was told no ceiling, so it can refuse nothing."""
    unlimited = OperationLimit(per_second=None, per_minute=None, per_hour=None)
    return PeriodMetadataReader(
        cache=cache,
        budget=QueryBudget(
            limits=RateLimits(
                metadata_queries=unlimited,
                exports=unlimited,
                export_statuses=unlimited,
                invoice_downloads=unlimited,
            ),
            clock=lambda: ASKED_AT,
        ),
    )


def test_a_window_too_big_for_one_page_comes_back_whole(
    reader: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert len(answer.page.invoices) == 2


def test_the_page_behind_the_first_is_asked_for_by_its_number(
    reader: PeriodMetadataReader, paging: CountingSession
) -> None:
    reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert paging.offsets == [0, 1]


def test_a_window_fetched_to_the_end_stops_saying_there_is_more(
    reader: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert answer.page.has_more is False


def test_every_further_page_is_paid_for_from_the_hourly_allowance(
    reader: PeriodMetadataReader, paging: CountingSession, budget: QueryBudget
) -> None:
    reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert budget.remaining(Operation.METADATA_QUERY) == 18


def test_a_whole_window_carries_the_number_of_the_last_page_it_holds(
    reader: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert answer.page.page_offset == 1


def test_a_whole_window_is_not_blamed_on_the_allowance(
    reader: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert answer.page.budget_bound is False


def test_the_loop_stops_before_it_drinks_the_other_subject_types_share(
    scarce: PeriodMetadataReader, paging: CountingSession
) -> None:
    scarce.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert paging.offsets == [0]


def test_a_window_the_allowance_cut_short_says_that_is_what_happened(
    scarce: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = scarce.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert answer.page.budget_bound is True


def test_a_window_the_allowance_cut_short_still_says_there_is_more(
    scarce: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = scarce.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert answer.page.has_more is True


def test_a_window_the_allowance_cut_short_keeps_the_page_that_did_arrive(
    scarce: PeriodMetadataReader, paging: CountingSession
) -> None:
    answer = scarce.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert len(answer.page.invoices) == 1


def test_a_counter_that_can_refuse_nothing_does_not_get_to_page_forever(
    uncounted: PeriodMetadataReader, paging: CountingSession
) -> None:
    uncounted.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert paging.offsets == [0]


def test_a_window_the_allowance_cut_short_is_not_written_to_disk(
    scarce: PeriodMetadataReader, paging: CountingSession, cache: PeriodCache
) -> None:
    scarce.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_the_call_after_a_cut_window_finishes_it_instead_of_serving_the_stump(
    scarce: PeriodMetadataReader,
    reader: PeriodMetadataReader,
    paging: CountingSession,
) -> None:
    scarce.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    answer = reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert len(answer.page.invoices) == 2


def test_a_window_ksef_itself_cut_short_is_not_written_to_disk(
    reader: PeriodMetadataReader, cache: PeriodCache
) -> None:
    stopped = CountingSession(
        page=MetadataPage(
            invoices=(synthetic_metadata(1),),
            has_more=False,
            truncated=True,
            hwm_date=None,
        )
    )

    reader.read(session=stopped, period=SEPTEMBER, direction=InvoiceDirection.BUYER)

    assert cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER) is None


def test_a_remembered_window_carries_the_page_number_it_reached(
    reader: PeriodMetadataReader, paging: CountingSession, cache: PeriodCache
) -> None:
    reader.read(session=paging, period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    kept = cache.remembered(period=SEPTEMBER, direction=InvoiceDirection.BUYER)
    assert kept is not None

    assert kept.page.page_offset == 1


def _erase(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), key=lambda found: -len(found.parts)):
        path.rmdir() if path.is_dir() else path.unlink()
    directory.rmdir()
