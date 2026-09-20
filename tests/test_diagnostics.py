"""What a refusal is allowed to say, and what only the journal may know.

Every number here is invented by `synthetic`. The first family of assertions is
about D-038: the handle a client sees distinguishes invoices, is stable between
runs, and gives back neither the NIP the number opens with nor the date that
follows it.

The second is about the journal itself (GH-116) — that it reaches stderr and
never stdout, that the file it may also write is opt-in and readable by nobody
else, and that configuring it twice does not leave the first configuration
behind.

The third is about correlation (GH-117): one tool call stamps all its records
with one identifier, two calls are told apart, and a call that ended leaves
nothing behind for the next one to inherit.
"""

import logging
import stat
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from ksef_mcp.diagnostics import (
    LOG_DIRECTORY_VARIABLE,
    LOG_FILE,
    LOG_FILE_BYTES,
    LOG_FILE_KEPT,
    LOG_FILE_MODE,
    LOGGER_NAME,
    REFERENCE_LENGTH,
    REFERENCE_PREFIX,
    UNCORRELATED,
    configure_diagnostics,
    correlated,
    correlation,
    requested_log_directory,
    short_reference,
    technical_log,
)
from ksef_mcp.metadata import SERVER_NAME
from synthetic import synthetic_number


@pytest.fixture(autouse=True)
def journal_put_back(journal_restored: None) -> None:
    """Every test here configures the journal, so every test here returns it."""


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


def test_the_journal_writes_to_stderr_because_stdout_carries_the_protocol() -> None:
    # One log line on stdout ends the stdio session: the client is parsing that
    # stream for JSON-RPC frames.
    log = configure_diagnostics()

    (handler,) = log.handlers
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert handler.stream is not sys.stdout


def test_the_journal_never_hands_records_up_to_the_root_logger() -> None:
    # An application embedding this package configures its own root, and some
    # of those roots write to stdout.
    log = configure_diagnostics()

    assert log.propagate is False
    assert log.level == logging.INFO


def test_configuring_the_journal_twice_leaves_one_set_of_handlers() -> None:
    configure_diagnostics()

    log = configure_diagnostics()

    assert len(log.handlers) == 1


@pytest.mark.parametrize(
    "stated",
    ["", "   ", "0"],
    ids=["unset", "blank", "switched-off"],
)
def test_no_file_journal_is_written_unless_one_was_asked_for(stated: str) -> None:
    # Off by default: a file nobody asked for leaves KSeF numbers on a disk
    # whose backup policy nobody considered.
    assert requested_log_directory({LOG_DIRECTORY_VARIABLE: stated}) is None


def test_an_absent_variable_means_no_file_journal() -> None:
    assert requested_log_directory({}) is None


def test_the_bare_switch_puts_the_file_journal_in_the_data_directory() -> None:
    # So enabling it does not require knowing where platformdirs puts things.
    asked = requested_log_directory({LOG_DIRECTORY_VARIABLE: "1"})

    assert asked is not None
    assert asked.name == SERVER_NAME


def test_a_stated_directory_is_taken_as_given(tmp_path: Path) -> None:
    assert requested_log_directory({LOG_DIRECTORY_VARIABLE: str(tmp_path)}) == tmp_path


def test_a_directory_under_the_home_shorthand_is_expanded() -> None:
    asked = requested_log_directory({LOG_DIRECTORY_VARIABLE: "~/dzienniki"})

    assert asked == Path.home() / "dzienniki"


def test_the_file_journal_is_readable_by_nobody_but_its_owner(tmp_path: Path) -> None:
    # It carries KSeF numbers in full, so it is exactly as sensitive as the
    # archive it describes.
    log = configure_diagnostics(directory=tmp_path / "dziennik")

    written = tmp_path / "dziennik" / LOG_FILE
    assert len(log.handlers) == 2
    assert stat.S_IMODE(written.stat().st_mode) == LOG_FILE_MODE


def test_the_file_journal_rotates_rather_than_growing_without_end(tmp_path: Path) -> None:
    log = configure_diagnostics(directory=tmp_path)

    rotating = log.handlers[1]
    assert isinstance(rotating, RotatingFileHandler)
    assert rotating.maxBytes == LOG_FILE_BYTES
    assert rotating.backupCount == LOG_FILE_KEPT


def test_a_failed_pass_is_recoverable_from_the_journal(tmp_path: Path) -> None:
    # The point of GH-116: the sentence the client saw is gone with the
    # conversation, and the file is what remains to reconstruct the pass from
    # without spending another export on it.
    configure_diagnostics(directory=tmp_path)

    technical_log().warning("Export %s stayed on disk.", "EXP-1")

    assert "Export EXP-1 stayed on disk." in (tmp_path / LOG_FILE).read_text(encoding="utf-8")


def test_nothing_outside_a_tool_call_claims_to_belong_to_one() -> None:
    assert correlation() == UNCORRELATED


def test_one_tool_call_stamps_all_its_records_with_one_identifier(tmp_path: Path) -> None:
    # The point of GH-117: eight records sharing a timestamp cannot be told
    # apart from another process's eight, and an identifier can.
    configure_diagnostics(directory=tmp_path)

    with correlated() as minted:
        technical_log().info("KSeF request: first")
        technical_log().info("KSeF request: second")

    written = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert written.count(f"[{minted}]") == 2


def test_two_tool_calls_are_told_apart_by_their_identifiers() -> None:
    with correlated() as first:
        pass
    with correlated() as second:
        pass

    assert first != second


def test_a_call_that_ended_leaves_no_identifier_for_the_next_one() -> None:
    # A leaked identifier is worse than none: it files the next call's records
    # under the previous call's name, and that reads as evidence.
    with correlated():
        pass

    assert correlation() == UNCORRELATED


def test_a_record_minted_outside_a_call_is_still_written(tmp_path: Path) -> None:
    # Configuration read at startup belongs to no tool call, and a formatter
    # missing the field would raise inside logging rather than log.
    configure_diagnostics(directory=tmp_path)

    technical_log().info("Configuration read.")

    assert f"[{UNCORRELATED}]" in (tmp_path / LOG_FILE).read_text(encoding="utf-8")


def test_configuring_the_journal_twice_leaves_one_stamp_per_record(tmp_path: Path) -> None:
    configure_diagnostics(directory=tmp_path)

    log = configure_diagnostics(directory=tmp_path)

    assert len(log.filters) == 1
