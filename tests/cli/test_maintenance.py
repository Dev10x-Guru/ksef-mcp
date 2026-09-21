from pathlib import Path

import pytest

from ksef_mcp import cli
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.storage.archive import InvoiceArchive
from ksef_mcp.storage.audit import AuditTrail, AuthorisationBasis, Disclosure
from tests.cli.conftest import DEPARTED_NIP, NIP, Recorder
from tests.test_retention import a_number as a_ksef_number
from tests.test_retention import a_package as retention_package


@pytest.fixture
def archive_root(subject_data_root: Path) -> Path:
    # One root for the trail and the archive both, so the purge writes its entry
    # beside the archive it emptied rather than into the data directory of
    # whoever runs the suite. The autouse fixture already substitutes it in
    # `paths`, which is the single place every store reads the layout from.
    return subject_data_root


@pytest.fixture
def archived_invoices(archive_root: Path) -> InvoiceArchive:
    stored = InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST)
    stored.store(package=retention_package(1, 2, 3))
    return stored


def purged_number(ordinal: int) -> str:
    return a_ksef_number(ordinal)


def test_purge_refuses_without_configuration(configuration_file: Path) -> None:
    recorder = Recorder()

    code = cli.main(["purge"], console=recorder.console, configuration_file=configuration_file)

    assert (code, "ksef-mcp onboarding" in recorder.transcript) == (
        cli.EXIT_NOT_CONFIGURED,
        True,
    )


@pytest.mark.parametrize("traversal", ["../../..", "../1234567890", "1234567890/../.."])
def test_purge_refuses_a_nip_that_points_outside_the_subject_tree(
    configured: Path,
    archived_invoices: InvoiceArchive,
    traversal: str,
) -> None:
    # GH-112. Everything this touches is under `tmp_path` — the archive fixture
    # writes there and the refusal has to come before any plan is built, so no
    # deletion is even considered.
    recorder = Recorder()

    code = cli.main(
        ["purge", "--nip", traversal],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, len(list(archived_invoices.invoice_directory.iterdir()))) == (
        cli.EXIT_INVALID_NIP,
        3,
    )


def test_purge_says_it_touched_nothing_when_it_refuses_the_nip(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder()

    cli.main(
        ["purge", "--nip", "../../.."], console=recorder.console, configuration_file=configured
    )

    assert "nie ruszam żadnego katalogu" in recorder.transcript


def test_purge_never_asks_for_confirmation_on_a_refused_nip(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    # The confirmation prompt is a guard against a mistake, not against bad
    # input. A refusal that reached it would be asking the person to approve a
    # plan the tool should never have drawn up.
    recorder = Recorder()

    cli.main(
        ["purge", "--nip", "../../.."], console=recorder.console, configuration_file=configured
    )

    assert recorder.prompts == []


def test_purge_accepts_a_grouped_nip_as_the_same_subject(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    code = cli.main(
        ["purge", "--nip", "123-456-78-90", "--do", "2025-12-31"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, "123-456-78-90" in recorder.transcript) == (cli.EXIT_OK, False)


def test_purge_refuses_a_window_that_ends_before_it_begins(configured: Path) -> None:
    recorder = Recorder()

    code = cli.main(
        ["purge", "--od", "2026-01-01", "--do", "2025-01-01"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, "covers no day" in recorder.transcript) == (cli.EXIT_INVALID_WINDOW, True)


def test_purge_deletes_nothing_when_the_window_matches_nothing(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder()

    code = cli.main(
        ["purge", "--do", "2020-01-01"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, "Nic nie pasuje" in recorder.transcript) == (cli.EXIT_OK, True)


def test_purge_names_every_invoice_before_asking(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["n"])

    cli.main(
        ["purge", "--do", "2024-12-31"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert purged_number(1) in recorder.transcript


def test_purge_keeps_the_archive_when_the_answer_is_no(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["n"])

    code = cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert (code, len(list(archived_invoices.invoice_directory.iterdir()))) == (
        cli.EXIT_PURGE_DECLINED,
        3,
    )


def test_purge_deletes_the_bodies_once_confirmed(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    code = cli.main(
        ["purge", "--do", "2025-12-31"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, sorted(path.stem for path in archived_invoices.invoice_directory.iterdir())) == (
        cli.EXIT_OK,
        [purged_number(3)],
    )


def test_purge_leaves_the_deduplication_index_untouched(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert archived_invoices.load_index().known == frozenset(
        purged_number(ordinal) for ordinal in (1, 2, 3)
    )


def test_purge_writes_the_deletion_to_the_audit_trail(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    recorded = AuditTrail(nip=NIP, environment=KsefEnvironment.TEST).entries()
    assert [(entry.operation, entry.disclosure, entry.document_count) for entry in recorded] == [
        ("purge_archive", Disclosure.REMOVAL, 3)
    ]


def test_purge_records_a_human_at_a_terminal_as_the_basis(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    recorded = AuditTrail(nip=NIP, environment=KsefEnvironment.TEST).entries()
    assert recorded[0].authorisation.basis is AuthorisationBasis.OPERATOR


def test_purge_reports_that_the_index_still_remembers(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert "pamięta nadal 3 numerów KSeF" in recorder.transcript


def test_purge_cuts_another_subject_when_asked_for_one(
    configured: Path,
    archive_root: Path,
) -> None:
    departed = InvoiceArchive(nip=DEPARTED_NIP, environment=KsefEnvironment.TEST)
    departed.store(package=retention_package(1, 2, 3))
    kept = InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST)
    kept.store(package=retention_package(1, 2, 3))
    recorder = Recorder(answers=["t"])

    cli.main(
        ["purge", "--nip", DEPARTED_NIP],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (
        list(departed.invoice_directory.iterdir()),
        len(list(kept.invoice_directory.iterdir())),
    ) == ([], 3)


def test_purge_warns_about_a_file_it_will_not_delete(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    (archived_invoices.invoice_directory / "notatka.xml").write_bytes(b"<x/>")
    recorder = Recorder(answers=["n"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert "notatka.xml" in recorder.transcript


@pytest.mark.parametrize(
    ("arguments", "described"),
    [
        ([], "od początku archiwum do dziś"),
        (["--od", "2025-01-01"], "od 2025-01-01 do dziś"),
        (["--do", "2025-01-01"], "od początku archiwum do 2025-01-01"),
        (["--od", "2024-01-01", "--do", "2025-01-01"], "od 2024-01-01 do 2025-01-01"),
    ],
)
def test_purge_restates_the_window_it_was_given(
    configured: Path,
    archived_invoices: InvoiceArchive,
    arguments: list[str],
    described: str,
) -> None:
    recorder = Recorder(answers=["n"])

    cli.main(["purge", *arguments], console=recorder.console, configuration_file=configured)

    assert described in recorder.transcript
