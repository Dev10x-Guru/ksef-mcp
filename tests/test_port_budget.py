from collections import deque
from datetime import UTC, datetime, timedelta

import pytest

from ksef_mcp.ksef_port import KsefRequestRejected, Operation, OperationLimit, RateLimits
from ksef_mcp.ksef_port.budget import QueryBudget

START = datetime(2026, 9, 1, 12, tzinfo=UTC)

UNSTATED = OperationLimit(per_second=None, per_minute=None, per_hour=None)


class MovableClock:
    def __init__(self) -> None:
        self.reading = START

    def __call__(self) -> datetime:
        return self.reading

    def advance(self, by: timedelta) -> None:
        self.reading += by


class RecordingJournal:
    """A journal that keeps the last picture it was handed, and nothing else."""

    def __init__(self, *, loaded: dict[Operation, tuple[datetime, ...]] | None = None) -> None:
        self.loaded = {} if loaded is None else loaded
        self.recorded: list[dict[Operation, tuple[datetime, ...]]] = []

    def load(self) -> dict[Operation, tuple[datetime, ...]]:
        return self.loaded

    def record(self, spent: dict[Operation, tuple[datetime, ...]]) -> None:
        self.recorded.append(spent)


def granted(*, metadata: int | None, exports: int, downloads: int) -> RateLimits:
    return RateLimits(
        metadata_queries=OperationLimit(per_second=8, per_minute=16, per_hour=metadata),
        exports=OperationLimit(per_second=None, per_minute=None, per_hour=exports),
        export_statuses=OperationLimit(per_second=None, per_minute=None, per_hour=exports),
        invoice_downloads=OperationLimit(per_second=None, per_minute=None, per_hour=downloads),
    )


def silent_about(operation: str) -> RateLimits:
    """KSeF naming no ceiling at all for one family, and real ones for the rest."""
    stated = granted(metadata=20, exports=20, downloads=64)
    return RateLimits(
        metadata_queries=UNSTATED if operation == "metadata" else stated.metadata_queries,
        exports=stated.exports,
        export_statuses=stated.export_statuses,
        invoice_downloads=stated.invoice_downloads,
    )


@pytest.fixture
def clock() -> MovableClock:
    return MovableClock()


@pytest.fixture
def budget(clock: MovableClock) -> QueryBudget:
    return QueryBudget(
        limits=granted(metadata=20, exports=20, downloads=64),
        clock=clock,
    )


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (Operation.METADATA_QUERY, 8),
        (Operation.EXPORT, 20),
        (Operation.INVOICE_DOWNLOAD, 64),
    ],
)
def test_the_allowance_is_whatever_ksef_granted(
    budget: QueryBudget,
    operation: Operation,
    expected: int,
) -> None:
    assert budget.remaining(operation) == expected


def test_spending_one_call_leaves_one_fewer(budget: QueryBudget) -> None:
    budget.spend(Operation.EXPORT)

    assert budget.remaining(Operation.EXPORT) == 19


def test_operations_do_not_share_an_allowance(budget: QueryBudget) -> None:
    budget.spend(Operation.METADATA_QUERY)

    assert budget.remaining(Operation.EXPORT) == 20


def test_a_spent_allowance_refuses_the_call_instead_of_letting_ksef_refuse_it(
    budget: QueryBudget,
) -> None:
    for _ in range(20):
        budget.spend(Operation.EXPORT)

    with pytest.raises(KsefRequestRejected):
        budget.spend(Operation.EXPORT)


def test_the_refusal_quotes_every_ceiling_so_the_tightest_one_is_visible(
    budget: QueryBudget,
) -> None:
    for _ in range(20):
        budget.spend(Operation.EXPORT)

    with pytest.raises(KsefRequestRejected, match="20 per hour"):
        budget.spend(Operation.EXPORT)


def test_the_window_slides_so_an_hour_old_call_stops_counting(
    budget: QueryBudget,
    clock: MovableClock,
) -> None:
    budget.spend(Operation.EXPORT)

    clock.advance(timedelta(hours=1, seconds=1))

    assert budget.remaining(Operation.EXPORT) == 20


def test_a_limit_ksef_does_not_state_is_not_invented(clock: MovableClock) -> None:
    budget = QueryBudget(limits=silent_about("metadata"), clock=clock)

    assert budget.remaining(Operation.METADATA_QUERY) is None


def test_the_default_clock_is_the_wall_clock_in_utc() -> None:
    counted = QueryBudget(limits=granted(metadata=20, exports=20, downloads=64))

    counted.spend(Operation.EXPORT)

    assert counted.remaining(Operation.EXPORT) == 19


def test_an_unstated_limit_never_blocks_a_call(clock: MovableClock) -> None:
    budget = QueryBudget(limits=silent_about("metadata"), clock=clock)

    budget.spend(Operation.METADATA_QUERY)

    assert budget.remaining(Operation.METADATA_QUERY) is None


def test_a_burst_inside_the_hour_is_refused_by_the_per_second_ceiling(
    budget: QueryBudget,
) -> None:
    for _ in range(8):
        budget.spend(Operation.METADATA_QUERY)

    with pytest.raises(KsefRequestRejected):
        budget.spend(Operation.METADATA_QUERY)


def test_a_second_later_the_per_second_ceiling_has_let_go(
    budget: QueryBudget,
    clock: MovableClock,
) -> None:
    for _ in range(8):
        budget.spend(Operation.METADATA_QUERY)

    clock.advance(timedelta(seconds=2))

    assert budget.remaining(Operation.METADATA_QUERY) == 8


def test_the_minute_ceiling_outlives_the_second_one(
    budget: QueryBudget,
    clock: MovableClock,
) -> None:
    for _ in range(16):
        budget.spend(Operation.METADATA_QUERY)
        clock.advance(timedelta(seconds=2))

    assert budget.remaining(Operation.METADATA_QUERY) == 0


def test_the_hourly_ceiling_is_what_answers_once_the_minute_has_passed(
    budget: QueryBudget,
    clock: MovableClock,
) -> None:
    for _ in range(16):
        budget.spend(Operation.METADATA_QUERY)
        clock.advance(timedelta(seconds=2))

    clock.advance(timedelta(minutes=2))

    assert budget.remaining(Operation.METADATA_QUERY) == 4


def test_a_counter_without_a_journal_starts_where_it_always_did(
    budget: QueryBudget,
) -> None:
    budget.spend(Operation.EXPORT)

    assert budget.remaining(Operation.EXPORT) == 19


def test_the_journal_hands_back_what_an_earlier_process_spent(clock: MovableClock) -> None:
    journal = RecordingJournal(loaded={Operation.EXPORT: (START - timedelta(minutes=5),) * 3})

    budget = QueryBudget(
        limits=granted(metadata=20, exports=20, downloads=64),
        clock=clock,
        journal=journal,
    )

    assert budget.remaining(Operation.EXPORT) == 17


def test_an_hour_of_tool_calls_can_exhaust_the_allowance_across_processes(
    clock: MovableClock,
) -> None:
    journal = RecordingJournal(loaded={Operation.EXPORT: (START - timedelta(minutes=5),) * 20})

    budget = QueryBudget(
        limits=granted(metadata=20, exports=20, downloads=64),
        clock=clock,
        journal=journal,
    )

    with pytest.raises(KsefRequestRejected):
        budget.spend(Operation.EXPORT)


def test_every_spend_reaches_the_journal_so_a_kill_loses_nothing(clock: MovableClock) -> None:
    journal = RecordingJournal()
    budget = QueryBudget(
        limits=granted(metadata=20, exports=20, downloads=64),
        clock=clock,
        journal=journal,
    )

    budget.spend(Operation.EXPORT)
    budget.spend(Operation.EXPORT)

    assert journal.recorded[-1] == {Operation.EXPORT: (START, START)}


def test_a_counter_handed_its_own_moments_does_not_also_load_the_journal(
    clock: MovableClock,
) -> None:
    journal = RecordingJournal(loaded={Operation.EXPORT: (START,) * 5})

    budget = QueryBudget(
        limits=granted(metadata=20, exports=20, downloads=64),
        clock=clock,
        spent={Operation.EXPORT: deque([START])},
        journal=journal,
    )

    assert budget.remaining(Operation.EXPORT) == 19
