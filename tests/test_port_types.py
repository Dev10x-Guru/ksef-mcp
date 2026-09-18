from datetime import UTC, date, datetime, timedelta

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
from ksef_mcp.ksef_port.types import MAX_QUERY_WINDOW, WIRE_SUBJECT_TYPES
from ksef_mcp.synchronisation import INITIAL_LOOKBACK
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


def test_a_ksef_number_carries_the_day_it_was_assigned() -> None:
    # Data otrzymania faktury, niezależna od momentu pobrania: nic w
    # InvoiceMetadata jej nie niesie, więc numer jest jedynym jej źródłem.
    assert KsefNumber(VALID_NUMBER).assigned_on == date(2026, 9, 1)


def test_eight_digits_that_are_not_a_date_are_refused() -> None:
    with pytest.raises(KsefRequestRejected, match="not a date"):
        KsefNumber("1234567890-20260231-0100AB12CD34-56")


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
        Period(
            date_from=NOON,
            date_to=NOON + MAX_QUERY_WINDOW + timedelta(days=1),
            date_type=DateType.ISSUE,
        )


def test_a_window_at_the_ceiling_is_accepted() -> None:
    period = Period(
        date_from=NOON,
        date_to=NOON + MAX_QUERY_WINDOW,
        date_type=DateType.ISSUE,
    )

    assert period.date_type is DateType.ISSUE


def test_synchronisation_pins_permanent_storage() -> None:
    period = Period.for_synchronisation(since=NOON, now=NOON)

    assert period.date_type is DateType.PERMANENT_STORAGE


def test_the_first_window_of_a_new_subject_fits_what_ksef_answers() -> None:
    """GH-84: the window a first run sends is the one that used to be unmeasured."""
    period = Period.for_synchronisation(since=NOON - INITIAL_LOOKBACK, now=NOON)

    assert period.date_to == NOON
    assert period.date_to - period.date_from <= MAX_QUERY_WINDOW


def test_a_point_older_than_the_ceiling_catches_up_rather_than_reaching_back() -> None:
    """A subject dormant for a year asks for a legal window, not an illegal one."""
    stale = NOON - timedelta(days=365)

    period = Period.for_synchronisation(since=stale, now=NOON)

    assert period.date_from == stale
    assert period.date_to == stale + MAX_QUERY_WINDOW


def test_the_first_lookback_can_never_outgrow_the_ceiling() -> None:
    """The two constants are one decision; drifting them apart is the GH-84 bug."""
    assert INITIAL_LOOKBACK <= MAX_QUERY_WINDOW


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
