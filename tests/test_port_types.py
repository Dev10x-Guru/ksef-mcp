from datetime import UTC, date, datetime, timedelta

import pytest

from ksef_mcp.ksef_port import (
    ContinuationPoint,
    DateType,
    ExportState,
    ExportStatus,
    InvalidKsefIdentifier,
    InvalidPeriod,
    KsefNumber,
    KsefPortError,
    MetadataPage,
    Period,
    SubjectRole,
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
    with pytest.raises(InvalidKsefIdentifier):
        KsefNumber(rejected)


def test_refusing_a_malformed_number_is_not_a_port_failure() -> None:
    """GH-170: nothing was sent, so nothing about the port can have failed.

    The distinction is not cosmetic. The period cache caught the port root to
    mean "damaged entry", and that root also covers an unreachable KSeF and a
    refused login — both of which were answered as a cache miss.
    """
    with pytest.raises(InvalidKsefIdentifier) as refusal:
        KsefNumber("FV/2026/09/001")

    assert not isinstance(refusal.value, KsefPortError)


def test_a_ksef_number_reads_back_as_written() -> None:
    assert str(KsefNumber(VALID_NUMBER)) == VALID_NUMBER


def test_a_ksef_number_carries_the_subject_it_was_issued_for() -> None:
    assert KsefNumber(VALID_NUMBER).issued_for_nip == "1234567890"


def test_a_ksef_number_carries_the_day_it_was_assigned() -> None:
    # Data otrzymania faktury, niezależna od momentu pobrania: nic w
    # InvoiceMetadata jej nie niesie, więc numer jest jedynym jej źródłem.
    assert KsefNumber(VALID_NUMBER).assigned_on == date(2026, 9, 1)


def test_eight_digits_that_are_not_a_date_are_refused() -> None:
    with pytest.raises(InvalidKsefIdentifier, match="not a date"):
        KsefNumber("1234567890-20260231-0100AB12CD34-56")


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(InvalidPeriod):
        Period(date_from=NOON, date_to=NOON - timedelta(days=1), date_type=DateType.ISSUE)


def test_a_window_wider_than_ksef_answers_is_refused_without_spending_a_call() -> None:
    with pytest.raises(InvalidPeriod):
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


def test_the_first_lookback_is_defined_from_the_ceiling_not_independently() -> None:
    """Strzeże definicji, nie zachowania: rozjazd tych dwóch stałych to błąd GH-84."""
    assert INITIAL_LOOKBACK == MAX_QUERY_WINDOW


@pytest.mark.parametrize(
    ("subject_role", "wire"),
    [
        (SubjectRole.SELLER, "Subject1"),
        (SubjectRole.BUYER, "Subject2"),
        (SubjectRole.THIRD_SUBJECT, "Subject3"),
        (SubjectRole.AUTHORIZED_SUBJECT, "SubjectAuthorized"),
    ],
)
def test_every_subject_role_has_its_wire_name(subject_role: SubjectRole, wire: str) -> None:
    assert WIRE_SUBJECT_TYPES[subject_role] == wire


def a_status(
    *,
    truncated: bool,
    hwm_date: datetime | None = None,
    last_permanent_storage_date: datetime | None = None,
) -> ExportStatus:
    return ExportStatus(
        state=ExportState.READY,
        parts=(),
        truncated=truncated,
        hwm_date=hwm_date,
        last_permanent_storage_date=last_permanent_storage_date,
        invoice_count=0,
    )


def test_a_truncated_package_continues_from_its_last_invoice() -> None:
    status = a_status(
        truncated=True,
        hwm_date=NOON + timedelta(days=2),
        last_permanent_storage_date=NOON + timedelta(days=1),
    )

    assert status.continuation_marker == NOON + timedelta(days=1)


def test_a_complete_package_continues_from_the_high_water_mark() -> None:
    status = a_status(
        truncated=False,
        hwm_date=NOON + timedelta(days=2),
        last_permanent_storage_date=NOON + timedelta(days=1),
    )

    assert status.continuation_marker == NOON + timedelta(days=2)


@pytest.mark.parametrize("truncated", [True, False], ids=["shortened", "whole"])
def test_a_package_whose_marker_ksef_left_empty_names_no_point(truncated: bool) -> None:
    assert a_status(truncated=truncated).continuation_marker is None


def test_a_continuation_point_stays_bound_to_its_subject_role() -> None:
    point = ContinuationPoint(subject_role=SubjectRole.THIRD_SUBJECT, reached=NOON)

    moved = point.advanced_to(marker=NOON + timedelta(days=1))

    assert (moved.subject_role, moved.reached) == (
        SubjectRole.THIRD_SUBJECT,
        NOON + timedelta(days=1),
    )


@pytest.mark.parametrize(
    ("has_more", "truncated", "expected"),
    [(False, False, True), (True, False, False), (False, True, False), (True, True, False)],
    ids=["whole-window", "ksef-has-more", "ksef-shortened", "both-signals"],
)
def test_a_page_knows_whether_it_is_the_whole_answer(
    has_more: bool,
    truncated: bool,
    expected: bool,
) -> None:
    page = MetadataPage(invoices=(), has_more=has_more, truncated=truncated, hwm_date=None)

    assert page.complete is expected


def test_a_shortened_page_carries_the_sentence_that_says_so() -> None:
    page = MetadataPage(invoices=(), has_more=True, truncated=False, hwm_date=None)

    assert "to nie jest komplet" in page.shortfall_note


def test_a_whole_page_adds_nothing_to_the_sentence_it_is_appended_to() -> None:
    page = MetadataPage(invoices=(), has_more=False, truncated=False, hwm_date=None)

    assert page.shortfall_note == ""


def test_a_page_carries_what_the_next_window_needs() -> None:
    page = MetadataPage(
        invoices=(synthetic_metadata(),),
        has_more=True,
        truncated=True,
        hwm_date=NOON,
    )

    assert (page.has_more, page.truncated, page.hwm_date) == (True, True, NOON)
