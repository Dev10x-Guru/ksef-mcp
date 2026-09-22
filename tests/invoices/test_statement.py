"""Whether a month can be handed to the accountant as one trustworthy file.

Four invariants are checked here, and all four follow from the file leaving
the machine. An amount travels from KSeF to the cell without losing a grosz
and without a float along the way. The working directory is a product, so
deleting it must not touch the archive or the cache (D-032) — and a directory
inside either of those roots is refused rather than silently accepted. KOD I
is derived from the digest of the file the archive actually holds, not from a
metadata row about that file. Gaps are named: an incomplete period, several
currencies, and rows with no file come back as warnings, because the CSV has
nowhere to hold a caveat (D-023).
"""

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from ksef_mcp.allowance import Allowance
from ksef_mcp.invoices.statement import (
    COLUMNS,
    NO_ARCHIVED_BODY,
    AccountingPeriod,
    Statement,
    StatementComposer,
    StatementRow,
    UnreadablePeriod,
    VerificationCode,
    WorkingDirectory,
    WorkingDirectoryRefused,
    amount,
    archived_digests,
    completeness_warning,
    correction_warning,
    corrections_matched,
    currency_warning,
    internal_root_conflict,
    prepare_working_directory,
    rendered,
    rows_for,
    statement_file_name,
    unverifiable_warning,
    verification_code,
    write_statement,
)
from ksef_mcp.ksef_port import (
    DateType,
    DocumentType,
    KsefEnvironment,
    KsefLimits,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
    SubjectRole,
)
from ksef_mcp.paths import SUBJECT_DIRECTORY
from ksef_mcp.storage.archive import InvoiceArchive, digest_of
from ksef_mcp.storage.period_cache import PeriodCache
from tests.conftest import an_allowance
from tests.support.synthetic import (
    SELLER_NIP,
    synthetic_body,
    synthetic_content_hash,
    synthetic_credential,
    synthetic_metadata,
    synthetic_number,
)

NIP = "1234567890"

CREDENTIAL = synthetic_credential()

ASKED_AT = datetime(2026, 9, 14, 7, 41, 17, tzinfo=UTC)

SEPTEMBER = AccountingPeriod(year=2026, month=9)

GENEROUS: Final[OperationLimit] = OperationLimit(per_second=None, per_minute=None, per_hour=20)


def limits() -> KsefLimits:
    return KsefLimits(
        rates=RateLimits(
            metadata_queries=GENEROUS,
            exports=GENEROUS,
            export_statuses=GENEROUS,
            invoice_downloads=GENEROUS,
        ),
        ceilings=SessionCeilings(
            max_invoice_megabytes=1,
            max_invoice_with_attachment_megabytes=3,
            max_invoices_per_session=10_000,
        ),
    )


def page_of(
    count: int,
    *,
    has_more: bool = False,
    truncated: bool = False,
) -> MetadataPage:
    return MetadataPage(
        invoices=tuple(synthetic_metadata(ordinal) for ordinal in range(1, count + 1)),
        has_more=has_more,
        truncated=truncated,
        hwm_date=None,
    )


@dataclass
class RecordingSession:
    """Counts how many of the twenty requests per hour actually reached KSeF."""

    page: MetadataPage
    asked: list[SubjectRole] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    # Pages beyond the first, keyed by their zero-based number, so a test can
    # put a real second page behind a `has_more` instead of miming one.
    further: dict[int, MetadataPage] = field(default_factory=dict)

    def read_limits(self) -> KsefLimits:
        return limits()

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        self.asked.append(subject_role)
        self.offsets.append(page_offset)
        return self.further.get(page_offset, self.page)


@dataclass
class RecordingPort:
    session_object: RecordingSession
    environment: KsefEnvironment = KsefEnvironment.TEST

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[RecordingSession]:
        yield self.session_object


@pytest.fixture
def archive(tmp_path: Path) -> InvoiceArchive:
    return InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path / "dane")


@pytest.fixture
def archived(archive: InvoiceArchive) -> InvoiceArchive:
    archive.invoice_directory.mkdir(mode=0o700, parents=True)
    (archive.invoice_directory / f"{synthetic_number(1)}.xml").write_bytes(synthetic_body(1))
    return archive


@pytest.fixture
def cache(tmp_path: Path) -> PeriodCache:
    return PeriodCache(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path / "cache",
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def working(tmp_path: Path) -> Path:
    return tmp_path / "robocze" / NIP


@pytest.fixture
def session() -> RecordingSession:
    return RecordingSession(page=page_of(2))


@pytest.fixture
def protection(tmp_path: Path) -> Allowance:
    return an_allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        root=tmp_path,
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def composer(
    session: RecordingSession,
    cache: PeriodCache,
    archived: InvoiceArchive,
    protection: Allowance,
) -> StatementComposer:
    return StatementComposer(
        port=RecordingPort(session_object=session),
        cache=cache,
        archive=archived,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )


@pytest.fixture
def statement(composer: StatementComposer, working: Path) -> Statement:
    return composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)


@pytest.fixture
def written(statement: Statement) -> str:
    return Path(statement.path).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1230.00"), "1230,00"),
        (Decimal("0.01"), "0,01"),
        (Decimal("-15.5"), "-15,5"),
        (Decimal("1E+3"), "1000"),
        (Decimal("12345678.99"), "12345678,99"),
    ],
)
def test_an_amount_reaches_the_cell_without_losing_a_grosz(value: Decimal, expected: str) -> None:
    # Exponential notation and rounding are two ways to drift from the
    # taxpayer's own application, neither of which anyone would blame on
    # the CSV generator.
    assert amount(value) == expected


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [("2026-01", (2026, 1)), ("2026-12", (2026, 12)), ("1999-08", (1999, 8))],
)
def test_a_period_reads_as_a_calendar_month(spelling: str, expected: tuple[int, int]) -> None:
    parsed = AccountingPeriod.parsed(spelling)

    assert (parsed.year, parsed.month) == expected


@pytest.mark.parametrize("spelling", ["2026-13", "2026-00", "sierpień", "2026-8", "2026"])
def test_a_period_that_cannot_be_read_is_refused(spelling: str) -> None:
    with pytest.raises(UnreadablePeriod, match="RRRR-MM"):
        AccountingPeriod.parsed(spelling)


def test_a_period_spells_itself_back_the_way_it_was_given() -> None:
    assert str(AccountingPeriod.parsed("2026-08")) == "2026-08"


def test_the_window_covers_the_whole_month() -> None:
    window = AccountingPeriod(year=2026, month=2).queried

    assert (window.date_from, window.date_to) == (
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, 23, 59, 59, 999999, tzinfo=UTC),
    )


def test_the_window_asks_by_issue_date() -> None:
    # The permanent-storage date would drop an invoice into the month KSeF
    # finished processing it, not the month it actually belongs to.
    assert SEPTEMBER.queried.date_type is DateType.ISSUE


def test_the_window_ends_on_the_last_day_of_the_month() -> None:
    # The statement's window must cover exactly the month asked for. Since
    # GH-84, checking "it has an end" would be a tautology — an end is
    # required by the type — so this test guards what can still break:
    # which day the window ends on.
    assert SEPTEMBER.queried.date_to.date() == date(2026, 9, 30)


def test_a_verification_code_is_made_of_three_parts() -> None:
    code = VerificationCode(
        seller_nip=SELLER_NIP,
        issue_date=date(2026, 9, 1),
        content_hash="abc123",
    )

    assert str(code) == f"{SELLER_NIP}-20260901-abc123"


def test_a_verification_code_takes_its_digest_from_the_index(archived: InvoiceArchive) -> None:
    code = verification_code(
        invoice=synthetic_metadata(1),
        archive=archived,
        digests=archived_digests(archived),
    )

    assert code is not None and code.content_hash == digest_of(synthetic_body(1))


def test_a_verification_code_falls_back_to_the_file_the_index_never_heard_of(
    archived: InvoiceArchive,
) -> None:
    """The index can be older than the directory it describes — a full read is the safety net."""
    code = verification_code(invoice=synthetic_metadata(1), archive=archived, digests={})

    assert code is not None and code.content_hash == digest_of(synthetic_body(1))


def test_an_unreadable_index_does_not_refuse_the_statement(archived: InvoiceArchive) -> None:
    """The index is this module's cache, not its source of truth — the files are."""
    archived.index_path.write_text(
        json.dumps({"schema_version": 99, "entries": []}), encoding="utf-8"
    )

    assert archived_digests(archived) == {}


def test_an_invoice_outside_the_archive_gets_no_code(archived: InvoiceArchive) -> None:
    """Content presence is checked on disk, never in the index: the code asserts bytes."""
    assert (
        verification_code(
            invoice=synthetic_metadata(2),
            archive=archived,
            digests={str(synthetic_metadata(2).ksef_number): "cudzy-skrot"},
        )
        is None
    )


def test_a_row_carries_the_eight_columns_the_currency_and_the_code(
    archived: InvoiceArchive,
) -> None:
    row = rows_for(invoices=(synthetic_metadata(1),), archive=archived)[0]

    assert row.cells == (
        str(synthetic_number(1)),
        "FV/2026/09/001",
        "2026-09-01",
        SELLER_NIP,
        "Dostawca sp. z o.o.",
        "1230,00",
        "1000,00",
        "230,00",
        "PLN",
        f"{SELLER_NIP}-20260901-{digest_of(synthetic_body(1))}",
    )


def test_a_seller_without_a_name_leaves_the_field_empty(archived: InvoiceArchive) -> None:
    row = StatementRow(invoice=synthetic_metadata(1, seller_name=None), code=None)

    assert row.cells[4] == ""


def test_a_row_with_no_archived_body_says_so_in_as_many_words() -> None:
    # An empty cell would look like an invoice whose digest was never computed.
    assert StatementRow(invoice=synthetic_metadata(1), code=None).cells[-1] == NO_ARCHIVED_BODY


def test_a_row_names_the_currency_its_amounts_are_in(archived: InvoiceArchive) -> None:
    # Without this, a month with an invoice in euros next to one in zloty
    # summed into a single number that meant nothing (#63).
    row = rows_for(invoices=(synthetic_metadata(1),), archive=archived)[0]

    assert row.cells[COLUMNS.index("Waluta")] == "PLN"


def test_the_file_opens_with_a_header_of_every_column() -> None:
    first = rendered(()).splitlines()[0]

    assert first.lstrip("﻿") == ";".join(COLUMNS)


def test_the_file_opens_with_a_byte_order_mark() -> None:
    # Without it, Polish Excel reads UTF-8 as a code page and counterparty
    # names come out garbled.
    assert rendered(()).startswith("﻿")


def test_the_file_name_says_what_the_attachment_is() -> None:
    assert statement_file_name(period=SEPTEMBER, nip=NIP) == "zestawienie-2026-09-1234567890.csv"


def test_the_file_name_carries_the_warning_about_a_shortened_period() -> None:
    # The warning in the tool's response never reaches the accountant — the file does.
    assert (
        statement_file_name(period=SEPTEMBER, nip=NIP, complete=False)
        == "zestawienie-2026-09-1234567890-NIEKOMPLETNE.csv"
    )


@pytest.mark.parametrize("root", ["dane", "cache"])
def test_a_directory_inside_internal_storage_is_refused(tmp_path: Path, root: str) -> None:
    with pytest.raises(WorkingDirectoryRefused, match="D-032"):
        prepare_working_directory(
            tmp_path / root / "zestawienia",
            data_root=tmp_path / "dane",
            cache_root_path=tmp_path / "cache",
        )


def test_the_data_root_itself_is_refused_too(tmp_path: Path) -> None:
    with pytest.raises(WorkingDirectoryRefused, match="D-032"):
        prepare_working_directory(
            tmp_path / "dane",
            data_root=tmp_path / "dane",
            cache_root_path=tmp_path / "cache",
        )


def test_a_directory_outside_both_roots_collides_with_neither(tmp_path: Path) -> None:
    assert (
        internal_root_conflict(
            tmp_path / "robocze",
            data_root=tmp_path / "dane",
            cache_root_path=tmp_path / "cache",
        )
        is None
    )


def test_a_new_working_directory_is_created_0700(working: Path) -> None:
    prepared = prepare_working_directory(working)

    assert (prepared.created, prepared.mode) == (True, 0o700)


def test_an_existing_directory_keeps_the_permissions_it_had(tmp_path: Path) -> None:
    # Someone may have pointed at a home directory or a shared one; silently
    # tightening its permissions is a change nobody asked for.
    existing = tmp_path / "wspolny"
    existing.mkdir(mode=0o755)

    prepared = prepare_working_directory(existing)

    assert prepared.warnings[0].startswith("Katalog roboczy istniał wcześniej")


def test_a_directory_already_0700_raises_no_caveat(working: Path) -> None:
    assert prepare_working_directory(working).warnings == ()


def test_a_cloud_synced_path_is_named_in_as_many_words(tmp_path: Path) -> None:
    prepared = prepare_working_directory(tmp_path / "OneDrive" / "ksef")

    assert "onedrive" in prepared.warnings[0]


def test_the_cloud_caveat_says_how_to_stop_hearing_it(tmp_path: Path) -> None:
    # GH-252: descriptive, and pointing at the one place the decision is made.
    prepared = prepare_working_directory(tmp_path / "OneDrive" / "ksef")

    assert "ksef-mcp onboarding" in prepared.warnings[0]


def test_the_caller_may_hand_over_its_own_judgement_of_syncing(tmp_path: Path) -> None:
    prepared = prepare_working_directory(
        tmp_path / "OneDrive" / "ksef",
        cloud_marker_for=lambda directory: None,
    )

    assert prepared.warnings == ()


def test_a_directory_with_no_cloud_marker_says_nothing_about_one() -> None:
    directory = WorkingDirectory(path=Path("/robocze"), created=True, mode=0o700, cloud_marker=None)

    assert directory.warnings == ()


def test_the_write_leaves_the_file_readable_only_by_its_owner(
    tmp_path: Path, archived: InvoiceArchive
) -> None:
    path = write_statement(
        rows=rows_for(invoices=(synthetic_metadata(1),), archive=archived),
        path=tmp_path / "zestawienie.csv",
    )

    assert path.stat().st_mode & 0o777 == 0o600


def test_the_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    write_statement(rows=(), path=tmp_path / "zestawienie.csv")

    assert sorted(one.name for one in tmp_path.iterdir()) == ["zestawienie.csv"]


@pytest.mark.parametrize(
    ("complete", "expected"),
    [(True, 0), (False, 1)],
)
def test_an_incomplete_period_is_said_in_as_many_words(complete: bool, expected: int) -> None:
    assert len(completeness_warning(complete=complete)) == expected


def test_a_spent_allowance_tells_the_accountant_to_ask_again() -> None:
    assert "Ponów za godzinę" in completeness_warning(complete=False, budget_bound=True)[0]


def test_a_period_cut_short_by_ksef_does_not_blame_the_allowance() -> None:
    assert "przydział" not in completeness_warning(complete=False, budget_bound=False)[0]


def test_a_complete_statement_leaves_the_sentence_with_the_sum_alone(statement: Statement) -> None:
    assert statement.shortfall == ""


def test_the_sentence_with_the_sum_says_itself_that_the_period_is_short(
    composer: StatementComposer, working: Path
) -> None:
    # This tool's product is the number sent to the accountant, and a sum
    # from part of a month looks exactly like a full one — the warning next
    # to it is often read after the decision, or never.
    composer.port.session_object.page = replace(
        composer.port.session_object.page,
        has_more=False,
        truncated=True,
    )

    result = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert result.message.startswith("Zestawienie za 2026-09") and "UWAGA" in result.message


def test_a_shortened_statement_carries_the_warning_in_its_file_name(
    composer: StatementComposer, working: Path
) -> None:
    composer.port.session_object.page = replace(
        composer.port.session_object.page,
        has_more=False,
        truncated=True,
    )

    result = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert result.path.endswith("-NIEKOMPLETNE.csv")


def test_a_statement_cut_short_by_ksef_does_not_blame_the_allowance(
    composer: StatementComposer, working: Path
) -> None:
    composer.port.session_object.page = replace(
        composer.port.session_object.page,
        has_more=False,
        truncated=True,
    )

    result = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert (result.budget_bound, "przydział" in result.message) == (False, False)


def test_a_spent_allowance_shows_in_the_sentence_with_the_sum(statement: Statement) -> None:
    starved = replace(statement, complete=False, budget_bound=True)

    assert "przydział" in starved.shortfall


def test_a_single_currency_needs_no_caveat(statement: Statement) -> None:
    assert currency_warning(statement.gross_totals) == ()


def test_several_currencies_caution_about_the_gross_column(
    composer: StatementComposer, working: Path
) -> None:
    # There is no currency column (settled in #41), so an invoice in EUR and
    # one in PLN put indistinguishable numbers in the Gross column.
    composer.port.session_object.page = MetadataPage(
        invoices=(synthetic_metadata(1), replace(synthetic_metadata(2), currency="EUR")),
        has_more=False,
        truncated=False,
        hwm_date=None,
    )

    result = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert any("kilku walutach" in one for one in result.warnings)


def test_a_period_without_corrections_needs_no_caveat(archived: InvoiceArchive) -> None:
    rows = rows_for(invoices=(synthetic_metadata(1), synthetic_metadata(2)), archive=archived)

    assert correction_warning(rows) == ()


def test_a_correction_without_its_original_invoice_cautions_about_the_gross_total(
    archived: InvoiceArchive,
) -> None:
    # A correction carries the difference against the invoice it corrects,
    # not that invoice restated (MF schema, P_15). With that invoice outside
    # the window there is nothing here for the difference to apply to, so the
    # sum is a sum of documents and not of liability (ADR-111).
    rows = rows_for(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(
                2,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(99),
            ),
        ),
        archive=archived,
    )

    assert correction_warning(rows)[0].startswith(
        "Okres zawiera korekty bez faktury pierwotnej w tym samym okresie (1 z 2"
    )


def test_a_correction_with_its_original_invoice_says_the_total_is_sound(
    archived: InvoiceArchive,
) -> None:
    # Both the difference and the amount it applies to are inside the window,
    # so the Brutto column adds up to the obligation by itself. This one is
    # news, not work — and the old single sentence could not tell the reader
    # which of the two she had (ADR-111).
    rows = rows_for(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(
                2,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(1),
            ),
        ),
        archive=archived,
    )

    assert correction_warning(rows)[0].startswith(
        "Okres zawiera korekty z fakturami pierwotnymi w tym samym okresie (1 z 2"
    )


def test_a_period_holding_both_kinds_of_correction_says_both(
    archived: InvoiceArchive,
) -> None:
    rows = rows_for(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(
                2,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(1),
            ),
            synthetic_metadata(
                3,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(99),
            ),
        ),
        archive=archived,
    )

    assert len(correction_warning(rows)) == 2


def test_the_caveat_about_the_missing_original_comes_first(
    archived: InvoiceArchive,
) -> None:
    # Only one of the two positions asks the accountant for work, and a
    # reader who stops after the first line has to meet that one.
    rows = rows_for(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(
                2,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(1),
            ),
            synthetic_metadata(
                3,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(99),
            ),
        ),
        archive=archived,
    )

    assert "bez faktury pierwotnej" in correction_warning(rows)[0]


def test_a_correction_pointing_at_nothing_counts_as_unpaired(
    archived: InvoiceArchive,
) -> None:
    # `hash_of_corrected_invoice` is `None` on a document that names no
    # original. Folding that absence into the lookup key would let it match
    # whatever else the window happened to hold.
    rows = rows_for(
        invoices=(synthetic_metadata(1, document_type=DocumentType.KOR),),
        archive=archived,
    )

    assert "bez faktury pierwotnej" in correction_warning(rows)[0]


def test_a_period_without_corrections_matches_nothing(archived: InvoiceArchive) -> None:
    rows = rows_for(invoices=(synthetic_metadata(1), synthetic_metadata(2)), archive=archived)

    assert corrections_matched(rows) == ()


def test_a_matched_correction_carries_the_invoice_it_corrects(
    archived: InvoiceArchive,
) -> None:
    rows = rows_for(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(
                2,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(1),
            ),
        ),
        archive=archived,
    )

    link = corrections_matched(rows)[0]

    assert (link.paired, link.corrected) == (True, rows[0])


def test_an_unmatched_correction_says_so_rather_than_reaching_for_ksef(
    archived: InvoiceArchive,
) -> None:
    # Reaching back for the missing invoice would mean guessing the window and
    # spending queries on the guess — refused by D-020 and D-031 §8.
    rows = rows_for(
        invoices=(
            synthetic_metadata(
                2,
                document_type=DocumentType.KOR,
                corrected_content_hash=synthetic_content_hash(99),
            ),
        ),
        archive=archived,
    )

    link = corrections_matched(rows)[0]

    assert (link.paired, link.corrected) == (False, None)


def test_a_correction_caveat_names_which_row_it_means(archived: InvoiceArchive) -> None:
    # Without a number, the reader hunts for the correction among a hundred
    # and thirty rows, and a warning that makes someone hunt is a warning
    # that gets skipped.
    rows = rows_for(
        invoices=(synthetic_metadata(7, document_type=DocumentType.KOR_ZAL),),
        archive=archived,
    )

    assert "FV/2026/09/007" in correction_warning(rows)[0]


@pytest.mark.parametrize(
    "document_type",
    [
        DocumentType.KOR,
        DocumentType.KOR_ZAL,
        DocumentType.KOR_ROZ,
        DocumentType.KOR_PEF,
        DocumentType.KOR_VAT_RR,
    ],
)
def test_every_kind_of_correction_is_recognised(
    archived: InvoiceArchive, document_type: DocumentType
) -> None:
    # FA, advance, settlement, PEF and FA_RR each have their own correction
    # kind. A check written against bare `kor` would pass and let four slip
    # through.
    rows = rows_for(
        invoices=(synthetic_metadata(1, document_type=document_type),), archive=archived
    )

    assert correction_warning(rows) != ()


@pytest.mark.parametrize(
    "document_type",
    [DocumentType.VAT, DocumentType.ZAL, DocumentType.ROZ, DocumentType.UPR],
)
def test_an_ordinary_document_is_not_a_correction(
    archived: InvoiceArchive, document_type: DocumentType
) -> None:
    rows = rows_for(
        invoices=(synthetic_metadata(1, document_type=document_type),), archive=archived
    )

    assert correction_warning(rows) == ()


def test_an_unrecognised_kind_does_not_pass_for_an_ordinary_invoice(
    archived: InvoiceArchive,
) -> None:
    # The table of kinds has already grown once. Staying silent about a
    # document this version cannot classify would let a future correction
    # kind slip through unnoticed.
    rows = rows_for(
        invoices=(synthetic_metadata(1, document_type=DocumentType.UNKNOWN),),
        archive=archived,
    )

    assert correction_warning(rows)[0].startswith("Nie rozpoznaję rodzaju 1 z 1")


def test_a_statement_with_a_correction_carries_the_caveat(
    composer: StatementComposer, working: Path
) -> None:
    composer.port.session_object.page = MetadataPage(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(2, document_type=DocumentType.KOR),
        ),
        has_more=False,
        truncated=False,
        hwm_date=None,
    )

    result = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert any("korekty bez faktury pierwotnej" in one for one in result.warnings)


def test_rows_with_no_archived_body_are_counted(archived: InvoiceArchive) -> None:
    rows = rows_for(invoices=(synthetic_metadata(1), synthetic_metadata(2)), archive=archived)

    assert unverifiable_warning(rows)[0].startswith("Bez KOD I: 1 z 2")


def test_rows_that_all_carry_a_code_raise_no_caveat(archived: InvoiceArchive) -> None:
    assert unverifiable_warning(rows_for(invoices=(synthetic_metadata(1),), archive=archived)) == ()


def test_the_statement_lands_in_the_working_directory(statement: Statement, working: Path) -> None:
    assert Path(statement.path).parent == working


def test_the_statement_counts_its_rows(statement: Statement) -> None:
    assert statement.row_count == 2


def test_the_statement_states_the_gross_total(statement: Statement) -> None:
    assert statement.gross_totals[0].gross == Decimal("2460.00")


def test_the_total_agrees_with_the_gross_column_of_the_file(
    statement: Statement, written: str
) -> None:
    rows = written.lstrip("﻿").strip().splitlines()[1:]
    summed = sum(Decimal(row.split(";")[5].replace(",", ".")) for row in rows)

    assert summed == statement.gross_totals[0].gross


def test_the_statement_asks_ksef_only_about_the_buyer_role(
    statement: Statement, session: RecordingSession
) -> None:
    # The counterparty in every one of the eight columns is the seller, so
    # the querying subject is the buyer — one query instead of four.
    assert session.asked == [SubjectRole.BUYER]


def test_a_second_pass_over_the_same_month_costs_no_query(
    composer: StatementComposer, working: Path, session: RecordingSession
) -> None:
    composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)
    composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert len(session.asked) == 1


def test_a_statement_answered_from_disk_says_it_came_from_disk(
    composer: StatementComposer, working: Path
) -> None:
    composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    repeated = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert repeated.from_cache is True


def test_the_first_pass_did_not_come_from_disk(statement: Statement) -> None:
    assert statement.from_cache is False


def test_the_statement_stamps_the_moment_it_asked(statement: Statement) -> None:
    assert statement.queried_at == ASKED_AT


def test_the_statement_names_the_environment_it_spoke_to(statement: Statement) -> None:
    assert statement.environment is KsefEnvironment.TEST


def test_the_message_carries_the_count_the_sum_and_the_path(statement: Statement) -> None:
    assert statement.message == (
        f"Zestawienie za 2026-09: 2 faktury, brutto 2460.00 PLN. Plik: {statement.path}"
    )


def test_an_empty_month_answers_with_a_message_and_no_sum(
    cache: PeriodCache, archived: InvoiceArchive, protection: Allowance
) -> None:
    composer = StatementComposer(
        port=RecordingPort(session_object=RecordingSession(page=page_of(0))),
        cache=cache,
        archive=archived,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )

    result = composer.run(
        nip=NIP,
        token=CREDENTIAL,
        period=SEPTEMBER,
        directory=archived.root.parent / "puste",
    )

    assert "brutto —" in result.message


def test_a_period_cut_short_comes_back_with_a_caveat(
    cache: PeriodCache, archived: InvoiceArchive, working: Path, protection: Allowance
) -> None:
    composer = StatementComposer(
        port=RecordingPort(session_object=RecordingSession(page=page_of(1, truncated=True))),
        cache=cache,
        archive=archived,
        allowance=protection,
        clock=lambda: ASKED_AT,
    )

    result = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert (result.complete, "nie jest kompletem" in result.warnings[0]) == (False, True)


def test_the_file_carries_no_invoice_content(written: str) -> None:
    # FA(2)/FA(3) is the counterparty's personal data and does not cross this
    # boundary (D-011) — the CSV gets the file's digest, never its content.
    assert "<Faktura" not in written


def test_the_file_carries_no_local_paths(written: str, archived: InvoiceArchive) -> None:
    # A local path is useless or misleading once uploaded; the tool's
    # response carries it, not the attachment (D-011).
    assert str(archived.invoice_directory) not in written


def test_deleting_the_working_directory_touches_neither_archive_nor_cache(
    statement: Statement, archived: InvoiceArchive, cache: PeriodCache, working: Path
) -> None:
    # The heart of D-032: the working directory is a product, the storage is internal.
    body = archived.invoice_directory / f"{synthetic_number(1)}.xml"
    remembered = cache.path_for(period=SEPTEMBER.queried, subject_role=SubjectRole.BUYER)

    shutil.rmtree(working)

    assert (body.is_file(), remembered.is_file(), Path(statement.path).exists()) == (
        True,
        True,
        False,
    )


def test_the_working_directory_lies_outside_the_data_root(
    statement: Statement, archived: InvoiceArchive
) -> None:
    assert not Path(statement.path).is_relative_to(archived.root / SUBJECT_DIRECTORY)
