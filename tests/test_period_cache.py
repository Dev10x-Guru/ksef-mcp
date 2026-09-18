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
from platformdirs import user_cache_path, user_data_path

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

# Wariant bez końca zostaje jako osobna atrapa: przychodzi już tylko z wpisu
# zapisanego, zanim pułap zaczął obowiązywać, i też nie może trafić do cache.
OPEN_ENDED = Period(
    date_from=datetime(2026, 9, 1, tzinfo=UTC),
    date_to=None,
    date_type=DateType.PERMANENT_STORAGE,
)

ASKED_AT = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)

GENEROUS = OperationLimit(per_second=None, per_minute=None, per_hour=20)


@dataclass
class CountingSession:
    """A session that says how many of the twenty an hour were actually spent."""

    page: MetadataPage
    asked: list[tuple[Period, InvoiceDirection]] = field(default_factory=list)

    def query_metadata(self, *, period: Period, direction: InvoiceDirection) -> MetadataPage:
        self.asked.append((period, direction))
        return self.page


@pytest.fixture
def page() -> MetadataPage:
    return MetadataPage(
        invoices=(synthetic_metadata(1), synthetic_metadata(2, seller_name=None)),
        has_more=True,
        truncated=True,
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


def test_an_open_window_still_has_a_key_to_look_up_by() -> None:
    assert cache_key(period=OPEN_ENDED, direction=InvoiceDirection.BUYER).startswith("buyer-")


def test_an_open_window_and_a_closed_one_are_different_questions() -> None:
    assert cache_key(period=OPEN_ENDED, direction=InvoiceDirection.BUYER) != cache_key(
        period=SEPTEMBER, direction=InvoiceDirection.BUYER
    )


@pytest.mark.parametrize(
    ("period", "expected"),
    [(SEPTEMBER, True), (OPEN_ENDED, False), (SYNCHRONISATION, False)],
)
def test_only_a_settled_period_is_worth_remembering(period: Period, expected: bool) -> None:
    assert is_cacheable(period) is expected


def test_a_synchronisation_window_is_refused_although_it_states_both_ends() -> None:
    """GH-84: the refusal follows the date type, not a missing end."""
    assert SYNCHRONISATION.date_to is not None
    assert is_cacheable(SYNCHRONISATION) is False


def test_an_open_window_leaves_nothing_behind(cache: PeriodCache, page: MetadataPage) -> None:
    cache.remember(period=OPEN_ENDED, direction=InvoiceDirection.BUYER, page=page)

    assert cache.remembered(period=OPEN_ENDED, direction=InvoiceDirection.BUYER) is None


def test_an_open_window_is_still_stamped_for_the_caller_to_report(
    cache: PeriodCache, page: MetadataPage
) -> None:
    stamped = cache.remember(period=OPEN_ENDED, direction=InvoiceDirection.BUYER, page=page)

    assert stamped.queried_at == ASKED_AT


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

    assert placed.directory.is_relative_to(cache_root())


def test_the_cache_root_is_the_platformdirs_one_for_this_server() -> None:
    assert cache_root() == user_cache_path(appname=SERVER_NAME)


def test_the_cache_root_is_not_the_data_root() -> None:
    placed = PeriodCache(nip=NIP, environment=KsefEnvironment.TEST)

    assert not placed.directory.is_relative_to(user_data_path(appname=SERVER_NAME))


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


def test_an_open_window_is_asked_about_every_time(
    reader: PeriodMetadataReader, session: CountingSession
) -> None:
    reader.read(session=session, period=OPEN_ENDED, direction=InvoiceDirection.BUYER)
    reader.read(session=session, period=OPEN_ENDED, direction=InvoiceDirection.BUYER)

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


def _erase(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), key=lambda found: -len(found.parts)):
        path.rmdir() if path.is_dir() else path.unlink()
    directory.rmdir()
