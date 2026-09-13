from datetime import UTC, datetime, timedelta

import pytest

from ksef_mcp.ksef_port import KsefRequestRejected, Operation, OperationLimit, RateLimits
from ksef_mcp.ksef_port.budget import QueryBudget

START = datetime(2026, 9, 1, 12, tzinfo=UTC)


class MovableClock:
    def __init__(self) -> None:
        self.reading = START

    def __call__(self) -> datetime:
        return self.reading

    def advance(self, by: timedelta) -> None:
        self.reading += by


def granted(*, metadata: int | None, exports: int, downloads: int) -> RateLimits:
    return RateLimits(
        metadata_queries=OperationLimit(per_second=8, per_minute=16, per_hour=metadata),
        exports=OperationLimit(per_second=None, per_minute=None, per_hour=exports),
        export_statuses=OperationLimit(per_second=None, per_minute=None, per_hour=exports),
        invoice_downloads=OperationLimit(per_second=None, per_minute=None, per_hour=downloads),
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
        (Operation.METADATA_QUERY, 20),
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
    budget.spend(Operation.METADATA_QUERY)

    assert budget.remaining(Operation.METADATA_QUERY) == 19


def test_operations_do_not_share_an_allowance(budget: QueryBudget) -> None:
    budget.spend(Operation.METADATA_QUERY)

    assert budget.remaining(Operation.EXPORT) == 20


def test_a_spent_allowance_refuses_the_call_instead_of_letting_ksef_refuse_it(
    budget: QueryBudget,
) -> None:
    for _ in range(20):
        budget.spend(Operation.METADATA_QUERY)

    with pytest.raises(KsefRequestRejected):
        budget.spend(Operation.METADATA_QUERY)


def test_the_window_slides_so_an_hour_old_call_stops_counting(
    budget: QueryBudget,
    clock: MovableClock,
) -> None:
    budget.spend(Operation.METADATA_QUERY)

    clock.advance(timedelta(hours=1, seconds=1))

    assert budget.remaining(Operation.METADATA_QUERY) == 20


def test_a_limit_ksef_does_not_state_is_not_invented(clock: MovableClock) -> None:
    budget = QueryBudget(
        limits=granted(metadata=None, exports=20, downloads=64),
        clock=clock,
    )

    assert budget.remaining(Operation.METADATA_QUERY) is None


def test_the_default_clock_is_the_wall_clock_in_utc() -> None:
    counted = QueryBudget(limits=granted(metadata=20, exports=20, downloads=64))

    counted.spend(Operation.METADATA_QUERY)

    assert counted.remaining(Operation.METADATA_QUERY) == 19


def test_an_unstated_limit_never_blocks_a_call(clock: MovableClock) -> None:
    budget = QueryBudget(
        limits=granted(metadata=None, exports=20, downloads=64),
        clock=clock,
    )

    budget.spend(Operation.METADATA_QUERY)

    assert budget.remaining(Operation.METADATA_QUERY) is None
