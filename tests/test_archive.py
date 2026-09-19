"""One invoice, one file, one number — and what happens when the run repeats.

Every package here is invented in the test: synthetic KSeF numbers, invoice
bodies that are not any taxpayer's, and a manifest this file writes. Nothing
reaches KSeF.

The assertions come in three families. Placement — a subdirectory per subject,
inside the data directory, readable only by its owner. Identity — the file is
named after the KSeF number the manifest states, never after the entry name in
the package. And repetition — a second run writes nothing, an invoice already
on disk is never written over, and an archive whose bodies were deleted still
refuses to fetch them again, because the index is a separate file (D-005).
"""

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from platformdirs import user_data_path

from ksef_mcp.archive import (
    ArchiveIndexUnreadable,
    ArchiveMetadataUnusable,
    ArchiveNotPerformed,
    ArchiveReport,
    InvoiceArchive,
    InvoiceIdentity,
    PackageArchivist,
    digest_of,
    identities,
)
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.package import ExportPackage, PackageDocument
from synthetic import base64_digest, synthetic_number

NIP = "1234567890"

ARCHIVED_AT = datetime(2026, 9, 14, 6, 30, tzinfo=UTC)

INVOICE = b"<Faktura><Naglowek>syntetyczna</Naglowek></Faktura>"

SCHEMA_AHEAD = 99


def a_body(ordinal: int) -> bytes:
    return INVOICE + str(ordinal).encode("ascii")


def an_entry(ordinal: int) -> dict[str, str]:
    return {
        "ksefNumber": str(synthetic_number(ordinal)),
        "fileName": f"faktura-{ordinal}.xml",
    }


def as_ksef_sends_it(ordinal: int) -> dict[str, str]:
    """One manifest entry in the shape the registry actually writes.

    `_metadata.json` holds `{"invoices": [InvoiceMetadata]}` — the same model
    `POST /invoices/query/metadata` returns — and `InvoiceMetadata` has no
    file-name field at all. What it does carry is `invoiceHash`, the SHA-256 of
    the invoice in base64, which is what pairs an entry with a document
    (GH-87). The fixture above invents a `fileName` MF never sends, which is
    why every mock test passed while production archived nothing.
    """
    return {
        "ksefNumber": str(synthetic_number(ordinal)),
        "invoiceHash": base64_digest(a_body(ordinal)),
    }


def a_manifest(*entries: dict[str, str]) -> bytes:
    return json.dumps({"invoices": list(entries)}).encode("utf-8")


def a_package(*ordinals: int) -> ExportPackage:
    return ExportPackage(
        reference="EXP-1",
        documents=tuple(
            PackageDocument(name=f"faktura-{ordinal}.xml", content=a_body(ordinal))
            for ordinal in ordinals
        ),
        metadata=a_manifest(*(an_entry(ordinal) for ordinal in ordinals)),
    )


def archived_path(archive: InvoiceArchive, ordinal: int) -> Path:
    return archive.invoice_directory / f"{synthetic_number(ordinal)}.xml"


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
    return a_package(1, 2)


@pytest.fixture
def stored(archive: InvoiceArchive, package: ExportPackage) -> ArchiveReport:
    return archive.store(package=package)


@pytest.fixture
def repeated(
    archive: InvoiceArchive, package: ExportPackage, stored: ArchiveReport
) -> ArchiveReport:
    return archive.store(package=package)


def a_package_as_ksef_sends_it(*ordinals: int) -> ExportPackage:
    return ExportPackage(
        reference="EXP-1",
        documents=tuple(
            PackageDocument(name=f"faktura-{ordinal}.xml", content=a_body(ordinal))
            for ordinal in ordinals
        ),
        metadata=a_manifest(*(as_ksef_sends_it(ordinal) for ordinal in ordinals)),
    )


def test_a_manifest_in_the_shape_ksef_sends_is_archived(archive: InvoiceArchive) -> None:
    """GH-87: production archived nothing because no entry ever named a file."""
    report = archive.store(package=a_package_as_ksef_sends_it(1, 2))

    assert report.archived == (str(synthetic_number(1)), str(synthetic_number(2)))


def test_an_invoice_paired_by_hash_lands_under_its_own_number(
    archive: InvoiceArchive,
) -> None:
    archive.store(package=a_package_as_ksef_sends_it(1, 2))

    assert archived_path(archive, 2).read_bytes() == a_body(2)


def test_each_invoice_lands_under_the_ksef_number_the_manifest_states(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert archived_path(archive, 1).read_bytes() == a_body(1)


def test_the_entry_name_from_the_package_is_not_used_as_a_file_name(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert list(archive.invoice_directory.glob("faktura-*")) == []


def test_the_report_names_every_number_it_archived(stored: ArchiveReport) -> None:
    assert stored.archived == (str(synthetic_number(1)), str(synthetic_number(2)))


def test_a_first_run_holds_nothing_already(stored: ArchiveReport) -> None:
    assert stored.already_held == ()


def test_the_report_carries_paths_and_never_an_invoice_body(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert stored.directory == str(archive.invoice_directory)


def test_an_archived_invoice_is_readable_only_by_its_owner(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert stat.S_IMODE(archived_path(archive, 1).stat().st_mode) == 0o600


def test_the_invoice_directory_is_readable_only_by_its_owner(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert stat.S_IMODE(archive.invoice_directory.stat().st_mode) == 0o700


def test_the_index_is_readable_only_by_its_owner(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert stat.S_IMODE(archive.index_path.stat().st_mode) == 0o600


def test_no_staging_file_outlives_the_write(archive: InvoiceArchive, stored: ArchiveReport) -> None:
    assert list(archive.directory.rglob("*.tmp")) == []


def test_the_index_remembers_the_number_and_the_digest_of_what_was_held(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert archive.load_index().entries[0].content_hash == digest_of(a_body(1))


def test_the_index_stamps_when_the_invoice_was_archived(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert archive.load_index().entries[0].archived_at == ARCHIVED_AT


def test_the_index_lives_beside_the_invoices_rather_than_among_them(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    assert archive.index_path.parent == archive.invoice_directory.parent


def test_a_repeated_package_archives_nothing_a_second_time(repeated: ArchiveReport) -> None:
    assert repeated.archived == ()


def test_a_repeated_package_reports_every_number_as_already_held(
    repeated: ArchiveReport,
) -> None:
    assert repeated.already_held == (str(synthetic_number(1)), str(synthetic_number(2)))


def test_the_index_does_not_grow_a_second_entry_for_the_same_invoice(
    archive: InvoiceArchive, repeated: ArchiveReport
) -> None:
    assert len(archive.load_index().entries) == 2


def test_an_invoice_already_on_disk_is_never_written_over(
    archive: InvoiceArchive, package: ExportPackage, stored: ArchiveReport
) -> None:
    archived_path(archive, 1).write_bytes(b"<Faktura>tknieta recznie</Faktura>")

    archive.store(package=package)

    assert archived_path(archive, 1).read_bytes() == b"<Faktura>tknieta recznie</Faktura>"


def test_a_deleted_body_is_not_fetched_back_because_the_index_remembers_it(
    archive: InvoiceArchive, package: ExportPackage, stored: ArchiveReport
) -> None:
    archived_path(archive, 1).unlink()
    archived_path(archive, 2).unlink()

    assert archive.store(package=package).archived == ()


def test_retention_leaves_no_body_behind_when_the_index_alone_decides(
    archive: InvoiceArchive, package: ExportPackage, stored: ArchiveReport
) -> None:
    archived_path(archive, 1).unlink()

    archive.store(package=package)

    assert archived_path(archive, 1).exists() is False


def test_a_body_without_an_index_entry_is_taken_as_held_rather_than_rewritten(
    archive: InvoiceArchive, package: ExportPackage, stored: ArchiveReport
) -> None:
    archive.index_path.unlink()

    assert archive.store(package=package).already_held == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


def test_a_body_without_an_index_entry_puts_that_number_back_in_the_index(
    archive: InvoiceArchive, package: ExportPackage, stored: ArchiveReport
) -> None:
    archive.index_path.unlink()

    archive.store(package=package)

    assert archive.load_index().known == {str(synthetic_number(1)), str(synthetic_number(2))}


def test_each_subject_gets_its_own_subdirectory(archive: InvoiceArchive, tmp_path: Path) -> None:
    other = InvoiceArchive(nip="9876543210", environment=KsefEnvironment.TEST, root=tmp_path)

    assert other.directory != archive.directory


def test_a_second_subject_does_not_see_the_first_subject_index(
    tmp_path: Path, stored: ArchiveReport
) -> None:
    other = InvoiceArchive(nip="9876543210", environment=KsefEnvironment.TEST, root=tmp_path)

    assert other.load_index().entries == ()


def test_the_demonstration_environment_archives_apart_from_the_test_one(
    archive: InvoiceArchive, tmp_path: Path
) -> None:
    demonstration = InvoiceArchive(nip=NIP, environment=KsefEnvironment.DEMO, root=tmp_path)

    assert demonstration.directory != archive.directory


def test_the_archive_lives_in_the_data_directory_not_the_cache_one() -> None:
    placed = InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST)

    assert placed.directory.is_relative_to(user_data_path(appname=SERVER_NAME))


def test_the_default_clock_stamps_a_moment_with_a_timezone(tmp_path: Path) -> None:
    unclocked = InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)

    unclocked.store(package=a_package(1))

    assert unclocked.load_index().entries[0].archived_at.tzinfo is not None


def test_an_archive_that_never_stored_anything_starts_with_an_empty_index(
    archive: InvoiceArchive,
) -> None:
    assert archive.load_index().entries == ()


def test_an_index_from_a_newer_build_is_refused_rather_than_guessed_at(
    archive: InvoiceArchive, stored: ArchiveReport
) -> None:
    document = json.loads(archive.index_path.read_text(encoding="utf-8"))
    document["schema_version"] = SCHEMA_AHEAD
    archive.index_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArchiveIndexUnreadable, match="Refusing to guess"):
        archive.load_index()


@pytest.mark.parametrize(
    ("metadata", "complaint"),
    [
        (None, "no _metadata.json"),
        (b"{", "not JSON"),
        (b'{"other": []}', "no list of invoices"),
        (b"5", "no list of invoices"),
        (b"[3]", "not an object"),
        (b'[{"fileName": "faktura-1.xml"}]', "no KSeF number"),
        (b'[{"ksefNumber": "1234567890-20260901-0100AB12CD01-56"}]', "points at no document"),
        (b'[{"ksefNumber": "FV/2026/09/001", "fileName": "a.xml"}]', "as a KSeF number"),
    ],
    ids=[
        "absent",
        "not-json",
        "no-invoice-list",
        "not-an-object-at-all",
        "entry-not-an-object",
        "no-number",
        "no-locator-at-all",
        "sellers-own-number",
    ],
)
def test_a_manifest_that_does_not_pair_numbers_with_files_is_refused(
    metadata: bytes | None, complaint: str
) -> None:
    with pytest.raises(ArchiveMetadataUnusable, match=complaint):
        identities(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        b'{"invoices": [{"ksefNumber": "1234567890-20260901-0100AB12CD01-56",'
        b' "fileName": "faktura-1.xml"}]}',
        b'{"faktury": [{"numerKSeF": "1234567890-20260901-0100AB12CD01-56",'
        b' "nazwaPliku": "faktura-1.xml"}]}',
        b'[{"ksef_number": "1234567890-20260901-0100AB12CD01-56", "file_name": "faktura-1.xml"}]',
    ],
    ids=["english-keys", "polish-keys", "top-level-list"],
)
def test_the_manifest_is_read_in_every_spelling_it_arrives_in(metadata: bytes) -> None:
    assert identities(metadata) == (
        InvoiceIdentity(
            ksef_number=synthetic_number(1),
            file_name="faktura-1.xml",
            content_hash=None,
        ),
    )


@pytest.mark.parametrize(
    "metadata",
    [
        b'{"invoices": [{"ksefNumber": "1234567890-20260901-0100AB12CD01-56",'
        b' "invoiceHash": "3q2+7w=="}]}',
        b'{"faktury": [{"numerKSeF": "1234567890-20260901-0100AB12CD01-56",'
        b' "skrotFaktury": "3q2+7w=="}]}',
    ],
    ids=["english-keys", "polish-keys"],
)
def test_a_digest_is_read_in_every_spelling_it_arrives_in(metadata: bytes) -> None:
    assert identities(metadata) == (
        InvoiceIdentity(
            ksef_number=synthetic_number(1),
            file_name=None,
            content_hash="3q2+7w==",
        ),
    )


def test_a_digest_the_package_does_not_carry_is_refused(archive: InvoiceArchive) -> None:
    astray = ExportPackage(
        reference="EXP-1",
        documents=(PackageDocument(name="faktura-1.xml", content=a_body(1)),),
        metadata=a_manifest(as_ksef_sends_it(2)),
    )

    with pytest.raises(ArchiveMetadataUnusable, match="does not carry"):
        archive.store(package=astray)


def test_two_documents_of_the_same_bytes_are_refused_rather_than_guessed(
    archive: InvoiceArchive,
) -> None:
    # A shared digest names neither document. Refusing is the same answer an
    # absent one gets; picking either would file an invoice under the other's
    # number, which is the whole thing the manifest exists to prevent.
    twinned = ExportPackage(
        reference="EXP-1",
        documents=(
            PackageDocument(name="pierwsza.xml", content=a_body(1)),
            PackageDocument(name="druga.xml", content=a_body(1)),
        ),
        metadata=a_manifest(as_ksef_sends_it(1), as_ksef_sends_it(1)),
    )

    with pytest.raises(ArchiveMetadataUnusable, match="does not carry"):
        archive.store(package=twinned)


def test_two_numbers_pointing_at_one_document_are_refused(archive: InvoiceArchive) -> None:
    contested = ExportPackage(
        reference="EXP-1",
        documents=(PackageDocument(name="faktura-1.xml", content=a_body(1)),),
        metadata=a_manifest(
            {"ksefNumber": str(synthetic_number(1)), "fileName": "faktura-1.xml"},
            {"ksefNumber": str(synthetic_number(2)), "fileName": "faktura-1.xml"},
        ),
    )

    with pytest.raises(ArchiveMetadataUnusable, match="One document cannot be two"):
        archive.store(package=contested)


def test_the_digest_wins_when_a_manifest_states_both_and_they_disagree(
    archive: InvoiceArchive,
) -> None:
    # A name can be restated wrongly; a digest of the bytes cannot be wrong
    # about which bytes it names.
    crossed = ExportPackage(
        reference="EXP-1",
        documents=(
            PackageDocument(name="pierwsza.xml", content=a_body(1)),
            PackageDocument(name="druga.xml", content=a_body(2)),
        ),
        metadata=a_manifest(
            {
                "ksefNumber": str(synthetic_number(1)),
                "invoiceHash": base64_digest(a_body(1)),
                "fileName": "druga.xml",
            },
            {
                "ksefNumber": str(synthetic_number(2)),
                "invoiceHash": base64_digest(a_body(2)),
                "fileName": "pierwsza.xml",
            },
        ),
    )

    archive.store(package=crossed)

    assert archived_path(archive, 1).read_bytes() == a_body(1)


def test_a_manifest_naming_a_file_the_package_lacks_is_refused(archive: InvoiceArchive) -> None:
    incomplete = ExportPackage(reference="EXP-1", documents=(), metadata=a_manifest(an_entry(1)))

    with pytest.raises(ArchiveMetadataUnusable, match="does not carry"):
        archive.store(package=incomplete)


def test_a_package_that_does_not_match_its_manifest_leaves_nothing_on_disk(
    archive: InvoiceArchive,
) -> None:
    incomplete = ExportPackage(reference="EXP-1", documents=(), metadata=a_manifest(an_entry(1)))

    with pytest.raises(ArchiveMetadataUnusable):
        archive.store(package=incomplete)

    assert archive.invoice_directory.exists() is False


def test_an_invoice_the_manifest_never_names_is_refused_rather_than_dropped(
    archive: InvoiceArchive,
) -> None:
    surplus = ExportPackage(
        reference="EXP-1",
        documents=(
            PackageDocument(name="faktura-1.xml", content=a_body(1)),
            PackageDocument(name="faktura-9.xml", content=a_body(9)),
        ),
        metadata=a_manifest(an_entry(1)),
    )

    with pytest.raises(ArchiveMetadataUnusable, match="does not name"):
        archive.store(package=surplus)


def test_the_archivist_keeps_the_report_the_retriever_throws_away(
    archive: InvoiceArchive, package: ExportPackage
) -> None:
    archivist = PackageArchivist(archive=archive)

    archivist(package)

    assert archivist.report == ArchiveReport(
        directory=str(archive.invoice_directory),
        index_path=str(archive.index_path),
        archived=(str(synthetic_number(1)), str(synthetic_number(2))),
        already_held=(),
    )


def test_the_archivist_hands_the_report_over_once_it_has_one(
    archive: InvoiceArchive, package: ExportPackage
) -> None:
    archivist = PackageArchivist(archive=archive)

    archivist(package)

    assert archivist.reported.archived == (str(synthetic_number(1)), str(synthetic_number(2)))


def test_an_archivist_asked_before_it_stored_anything_refuses(
    archive: InvoiceArchive,
) -> None:
    # Reading a report that does not exist would report an empty archive as a
    # successful one — the caller ran the two steps in the wrong order.
    archivist = PackageArchivist(archive=archive)

    with pytest.raises(ArchiveNotPerformed, match="has not stored a package yet"):
        archivist.reported  # noqa: B018
