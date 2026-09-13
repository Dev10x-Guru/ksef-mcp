from datetime import UTC, datetime, timedelta

import pytest

from ksef_mcp.ksef_port import (
    ContinuationPoint,
    DateType,
    InvoiceDirection,
    KsefNumber,
    KsefRequestRejected,
    MetadataPage,
    Period,
    SubjectContext,
)
from ksef_mcp.ksef_port.types import WIRE_SUBJECT_TYPES
from synthetic import synthetic_metadata

VALID_NUMBER = "1234567890-20260901-0100AB12CD34-56"

NOON = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "rejected",
    [
        "FV/2026/09/001",
        "1234567890-20260901-0100AB12CD34",
        "123456789-20260901-0100AB12CD34-56",
        "1234567890-2026-09-01-0100AB12CD34-56",
        "",
    ],
)
def test_a_number_that_is_not_a_ksef_number_is_refused(rejected: str) -> None:
    with pytest.raises(KsefRequestRejected):
        KsefNumber(rejected)


def test_a_ksef_number_reads_back_as_written() -> None:
    assert str(KsefNumber(VALID_NUMBER)) == VALID_NUMBER


def test_a_ksef_number_carries_the_subject_it_was_issued_for() -> None:
    assert KsefNumber(VALID_NUMBER).issued_for_nip == "1234567890"


@pytest.mark.parametrize("rejected", ["12345678", "12345678901", "123456789a"])
def test_a_subject_needs_a_ten_digit_nip(rejected: str) -> None:
    with pytest.raises(KsefRequestRejected):
        SubjectContext(rejected)


def test_a_subject_keeps_its_nip() -> None:
    assert SubjectContext("1234567890").nip == "1234567890"


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(KsefRequestRejected):
        Period(date_from=NOON, date_to=NOON - timedelta(days=1), date_type=DateType.ISSUE)


def test_a_window_wider_than_ksef_answers_is_refused_without_spending_a_call() -> None:
    with pytest.raises(KsefRequestRejected):
        Period(date_from=NOON, date_to=NOON + timedelta(days=101), date_type=DateType.ISSUE)


def test_a_window_at_the_ceiling_is_accepted() -> None:
    period = Period(
        date_from=NOON,
        date_to=NOON + timedelta(days=100),
        date_type=DateType.ISSUE,
    )

    assert period.date_type is DateType.ISSUE


def test_synchronisation_pins_permanent_storage_and_leaves_the_end_open() -> None:
    period = Period.for_synchronisation(since=NOON)

    assert (period.date_type, period.date_to) == (DateType.PERMANENT_STORAGE, None)


@pytest.mark.parametrize(
    ("direction", "wire"),
    [
        (InvoiceDirection.SELLER, "Subject1"),
        (InvoiceDirection.BUYER, "Subject2"),
        (InvoiceDirection.THIRD_SUBJECT, "Subject3"),
        (InvoiceDirection.AUTHORIZED_SUBJECT, "SubjectAuthorized"),
    ],
)
def test_every_direction_has_its_wire_name(direction: InvoiceDirection, wire: str) -> None:
    assert WIRE_SUBJECT_TYPES[direction] == wire


def test_a_truncated_package_continues_from_its_last_invoice() -> None:
    point = ContinuationPoint(direction=InvoiceDirection.BUYER, reached=NOON)

    moved = point.advanced_to(
        hwm=NOON + timedelta(days=2),
        truncated=True,
        last_seen=NOON + timedelta(days=1),
    )

    assert moved.reached == NOON + timedelta(days=1)


def test_a_complete_package_continues_from_the_high_water_mark() -> None:
    point = ContinuationPoint(direction=InvoiceDirection.BUYER, reached=NOON)

    moved = point.advanced_to(
        hwm=NOON + timedelta(days=2),
        truncated=False,
        last_seen=NOON + timedelta(days=1),
    )

    assert moved.reached == NOON + timedelta(days=2)


def test_a_continuation_point_stays_bound_to_its_subject_type() -> None:
    point = ContinuationPoint(direction=InvoiceDirection.THIRD_SUBJECT, reached=NOON)

    moved = point.advanced_to(hwm=NOON, truncated=False, last_seen=NOON)

    assert moved.direction is InvoiceDirection.THIRD_SUBJECT


def test_a_page_carries_what_the_next_window_needs() -> None:
    page = MetadataPage(
        invoices=(synthetic_metadata(),),
        has_more=True,
        truncated=True,
        hwm_date=NOON,
    )

    assert (page.has_more, page.truncated, page.hwm_date) == (True, True, NOON)
