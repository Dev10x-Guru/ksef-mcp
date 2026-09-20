"""What a refusal is allowed to say, and what only the journal may know.

Every number here is invented by `synthetic`. The assertions are about D-038:
the handle a client sees distinguishes invoices, is stable between runs, and
gives back neither the NIP the number opens with nor the date that follows it.
"""

import logging

from ksef_mcp.diagnostics import (
    LOGGER_NAME,
    REFERENCE_LENGTH,
    REFERENCE_PREFIX,
    short_reference,
    technical_log,
)
from synthetic import synthetic_number


def test_a_handle_carries_neither_the_nip_nor_the_date_of_the_number() -> None:
    number = str(synthetic_number(1))
    nip, issued, _identifier, _checksum = number.split("-")

    handle = short_reference(number)

    assert nip not in handle
    assert issued not in handle


def test_two_invoices_are_told_apart_by_their_handles() -> None:
    assert short_reference(str(synthetic_number(1))) != short_reference(str(synthetic_number(2)))


def test_the_same_invoice_keeps_one_handle_between_runs() -> None:
    # The handle is what support asks the person to quote back, so it has to
    # survive a restart — a random one would name a different thing each time.
    assert short_reference(str(synthetic_number(1))) == short_reference(str(synthetic_number(1)))


def test_a_handle_announces_itself_as_a_handle() -> None:
    handle = short_reference(str(synthetic_number(1)))

    assert handle.startswith(REFERENCE_PREFIX)
    assert len(handle) == len(REFERENCE_PREFIX) + REFERENCE_LENGTH


def test_the_journal_is_this_package_s_own_logger_and_not_the_root() -> None:
    journal = technical_log()

    assert journal is logging.getLogger(LOGGER_NAME)
    assert journal is not logging.getLogger()
