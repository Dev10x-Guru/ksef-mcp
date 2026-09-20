"""Kasowanie treści bez kasowania pamięci o tym, że była.

Cała wartość tego modułu mieści się w jednym niezmienniku: po wyczyszczeniu
archiwum ponowna synchronizacja **nie ściąga skasowanych faktur**. Test
`test_a_purged_invoice_is_never_fetched_again` sprawdza go wprost — kasuje,
uruchamia ścieżkę archiwizacji jeszcze raz i patrzy, czy na dysku nie ląduje
nic, co przed chwilą zniknęło.

Reszta rodzin asercji: oba wymiary cięcia (podmiot i okres), nietykalność
sąsiednich plików (indeks, punkty kontynuacji, dziennik przeglądu) i to, że
nieodwracalna operacja nie zgaduje — plik o nazwie, która nie jest numerem
KSeF, zostaje tam, gdzie leży.

Numery i treści są syntetyczne. Żaden bajt cudzej faktury nie dotyka zestawu.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ksef_mcp.archive import InvoiceArchive
from ksef_mcp.audit import AuditedOperation, Authorisation, AuthorisationBasis, Disclosure
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.package import ExportPackage, PackageDocument
from ksef_mcp.retention import (
    ArchivePurge,
    PurgeWindow,
    PurgeWindowInverted,
    purge_entry,
)
from tests.test_archive import a_manifest

NIP = "1234567890"

NEIGHBOUR_NIP = "9876543210"

ARCHIVED_AT = datetime(2026, 9, 14, 6, 30, tzinfo=UTC)

RECEIVED: dict[int, date] = {
    1: date(2024, 3, 5),
    2: date(2025, 7, 1),
    3: date(2026, 9, 1),
}

BODY = b"<Faktura><Naglowek>syntetyczna</Naglowek></Faktura>"


def a_number(ordinal: int) -> str:
    day = RECEIVED[ordinal].strftime("%Y%m%d")
    return f"{NIP}-{day}-0100AB12CD{ordinal:02d}-56"


def a_body(ordinal: int) -> bytes:
    return BODY + str(ordinal).encode("ascii")


def an_entry(ordinal: int) -> dict[str, str]:
    return {"ksefNumber": a_number(ordinal), "fileName": f"faktura-{ordinal}.xml"}


def a_package(*ordinals: int) -> ExportPackage:
    return ExportPackage(
        reference="EXP-1",
        documents=tuple(
            PackageDocument(name=f"faktura-{ordinal}.xml", content=a_body(ordinal))
            for ordinal in ordinals
        ),
        metadata=a_manifest(*(an_entry(ordinal) for ordinal in ordinals)),
    )


def body_path(archive: InvoiceArchive, ordinal: int) -> Path:
    return archive.invoice_directory / f"{a_number(ordinal)}.xml"


@pytest.fixture
def archive(tmp_path: Path) -> InvoiceArchive:
    return InvoiceArchive(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path,
        clock=lambda: ARCHIVED_AT,
    )


@pytest.fixture
def package() -> ExportPackage:
    return a_package(1, 2, 3)


@pytest.fixture
def filled(archive: InvoiceArchive, package: ExportPackage) -> InvoiceArchive:
    archive.store(package=package)
    return archive


@pytest.fixture
def purge(filled: InvoiceArchive) -> ArchivePurge:
    return ArchivePurge(archive=filled)


@pytest.mark.parametrize(
    ("window", "day", "covered"),
    [
        (PurgeWindow(), date(2024, 3, 5), True),
        (PurgeWindow(received_to=date(2025, 1, 1)), date(2024, 3, 5), True),
        (PurgeWindow(received_to=date(2025, 1, 1)), date(2025, 7, 1), False),
        (PurgeWindow(received_from=date(2025, 1, 1)), date(2024, 3, 5), False),
        (PurgeWindow(received_from=date(2025, 1, 1)), date(2025, 7, 1), True),
        (
            PurgeWindow(received_from=date(2025, 1, 1), received_to=date(2025, 12, 31)),
            date(2025, 7, 1),
            True,
        ),
        (
            PurgeWindow(received_from=date(2025, 1, 1), received_to=date(2025, 12, 31)),
            date(2026, 9, 1),
            False,
        ),
        (PurgeWindow(received_to=date(2025, 7, 1)), date(2025, 7, 1), True),
        (PurgeWindow(received_from=date(2025, 7, 1)), date(2025, 7, 1), True),
    ],
)
def test_the_window_says_which_days_it_covers(
    window: PurgeWindow,
    day: date,
    covered: bool,
) -> None:
    assert window.covers(day) is covered


def test_a_window_ending_before_it_begins_is_refused() -> None:
    with pytest.raises(PurgeWindowInverted, match="covers no day"):
        PurgeWindow(received_from=date(2026, 1, 1), received_to=date(2025, 1, 1))


@pytest.mark.parametrize(
    ("window", "criteria"),
    [
        (PurgeWindow(), "received .."),
        (PurgeWindow(received_from=date(2025, 1, 1)), "received 2025-01-01.."),
        (PurgeWindow(received_to=date(2025, 1, 1)), "received ..2025-01-01"),
        (
            PurgeWindow(received_from=date(2024, 1, 1), received_to=date(2025, 1, 1)),
            "received 2024-01-01..2025-01-01",
        ),
    ],
)
def test_the_window_states_itself_for_the_trail(window: PurgeWindow, criteria: str) -> None:
    assert window.criteria == criteria


def test_the_plan_names_every_body_the_window_covers(purge: ArchivePurge) -> None:
    plan = purge.plan(window=PurgeWindow(received_to=date(2025, 12, 31)))

    assert [candidate.ksef_number for candidate in plan.candidates] == [
        a_number(1),
        a_number(2),
    ]


def test_the_plan_names_what_stays(purge: ArchivePurge) -> None:
    plan = purge.plan(window=PurgeWindow(received_to=date(2025, 12, 31)))

    assert plan.retained == (a_number(3),)


def test_the_plan_measures_what_would_be_freed(purge: ArchivePurge) -> None:
    plan = purge.plan(window=PurgeWindow(received_from=date(2026, 1, 1)))

    assert plan.freed_bytes == len(a_body(3))


def test_the_plan_dates_each_body_by_the_day_it_reached_ksef(purge: ArchivePurge) -> None:
    plan = purge.plan(window=PurgeWindow(received_to=date(2024, 12, 31)))

    assert [candidate.received_on for candidate in plan.candidates] == [RECEIVED[1]]


def test_a_file_that_is_not_a_ksef_number_is_reported(purge: ArchivePurge) -> None:
    (purge.archive.invoice_directory / "notatka.xml").write_bytes(b"<x/>")

    plan = purge.plan(window=PurgeWindow())

    assert plan.unrecognised == ("notatka.xml",)


def test_a_file_that_is_not_a_ksef_number_is_left_alone(purge: ArchivePurge) -> None:
    stranger = purge.archive.invoice_directory / "notatka.xml"
    stranger.write_bytes(b"<x/>")

    purge.remove(plan=purge.plan(window=PurgeWindow()))

    assert stranger.is_file()


def test_removing_deletes_the_bodies_the_plan_named(purge: ArchivePurge) -> None:
    plan = purge.plan(window=PurgeWindow(received_to=date(2025, 12, 31)))

    purge.remove(plan=plan)

    assert [body_path(purge.archive, ordinal).exists() for ordinal in (1, 2, 3)] == [
        False,
        False,
        True,
    ]


def test_the_report_names_the_numbers_that_ceased_to_exist(purge: ArchivePurge) -> None:
    report = purge.remove(plan=purge.plan(window=PurgeWindow(received_to=date(2024, 12, 31))))

    assert report.purged == (a_number(1),)


def test_the_report_counts_what_the_index_still_remembers(purge: ArchivePurge) -> None:
    report = purge.remove(plan=purge.plan(window=PurgeWindow()))

    assert (report.still_known, report.freed_bytes) == (3, sum(len(a_body(o)) for o in (1, 2, 3)))


def test_the_report_carries_the_criteria_it_was_asked_for(purge: ArchivePurge) -> None:
    window = PurgeWindow(received_to=date(2024, 12, 31))

    report = purge.remove(plan=purge.plan(window=window))

    assert report.criteria == window.criteria


def test_the_deduplication_index_is_never_rewritten(purge: ArchivePurge) -> None:
    before = purge.archive.index_path.read_bytes()

    purge.remove(plan=purge.plan(window=PurgeWindow()))

    assert purge.archive.index_path.read_bytes() == before


def test_a_purged_invoice_is_never_fetched_again(
    purge: ArchivePurge,
    package: ExportPackage,
) -> None:
    """Niezmiennik z D-034: indeks przeżywa treść, więc nic nie wraca na dysk."""
    purge.remove(plan=purge.plan(window=PurgeWindow()))

    report = purge.archive.store(package=package)

    assert (report.archived, sorted(report.already_held)) == (
        (),
        sorted(a_number(ordinal) for ordinal in (1, 2, 3)),
    )


def test_a_purged_invoice_does_not_land_back_on_disk(
    purge: ArchivePurge,
    package: ExportPackage,
) -> None:
    purge.remove(plan=purge.plan(window=PurgeWindow()))

    purge.archive.store(package=package)

    assert [body_path(purge.archive, ordinal).exists() for ordinal in (1, 2, 3)] == [
        False,
        False,
        False,
    ]


def test_the_index_still_reports_the_purged_numbers_as_known(purge: ArchivePurge) -> None:
    purge.remove(plan=purge.plan(window=PurgeWindow()))

    assert purge.archive.load_index().known == frozenset(a_number(ordinal) for ordinal in (1, 2, 3))


def test_the_bookkeeping_beside_the_archive_is_left_alone(purge: ArchivePurge) -> None:
    neighbours = tuple(
        purge.archive.directory / name
        for name in ("synchronisation.json", "review.json", "audit.jsonl")
    )
    for neighbour in neighbours:
        neighbour.write_text("{}\n", encoding="utf-8")

    purge.remove(plan=purge.plan(window=PurgeWindow()))

    assert [neighbour.is_file() for neighbour in neighbours] == [True, True, True]


def test_a_purge_never_crosses_into_another_subject(
    purge: ArchivePurge,
    tmp_path: Path,
    package: ExportPackage,
) -> None:
    neighbour = InvoiceArchive(
        nip=NEIGHBOUR_NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path,
        clock=lambda: ARCHIVED_AT,
    )
    neighbour.store(package=package)

    purge.remove(plan=purge.plan(window=PurgeWindow()))

    assert sorted(path.name for path in neighbour.invoice_directory.iterdir()) == sorted(
        f"{a_number(ordinal)}.xml" for ordinal in (1, 2, 3)
    )


def test_the_audit_entry_records_a_removal_and_its_numbers(purge: ArchivePurge) -> None:
    report = purge.remove(plan=purge.plan(window=PurgeWindow(received_to=date(2024, 12, 31))))

    entry = purge_entry(
        report,
        authorisation=Authorisation(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            basis=AuthorisationBasis.OPERATOR,
        ),
        moment=ARCHIVED_AT,
    )

    assert (entry.operation, entry.disclosure, entry.ksef_numbers, entry.document_count) == (
        AuditedOperation.PURGE,
        Disclosure.REMOVAL,
        (a_number(1),),
        1,
    )


def test_the_audit_entry_carries_no_invoice_content(purge: ArchivePurge) -> None:
    report = purge.remove(plan=purge.plan(window=PurgeWindow()))

    entry = purge_entry(
        report,
        authorisation=Authorisation(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            basis=AuthorisationBasis.OPERATOR,
        ),
        moment=ARCHIVED_AT,
    )

    assert BODY.decode("utf-8") not in str(entry)
