"""Czy miesiąc da się oddać księgowej jednym plikiem, któremu można zaufać.

Sprawdzane są tu cztery niezmienniki i wszystkie cztery wynikają z tego, że plik
opuszcza maszynę. Kwota przechodzi z KSeF-u do komórki bez straty grosza i bez
float-a po drodze. Katalog roboczy jest produktem, więc jego skasowanie nie
rusza ani archiwum, ani cache (D-032) — i katalog wewnątrz któregokolwiek z tych
korzeni jest odrzucany, zamiast być cicho przyjęty. KOD I powstaje ze skrótu
pliku, który archiwum naprawdę trzyma, a nie z wiersza metadanych o tym pliku.
Braki są nazwane: niekompletny okres, kilka walut i pozycje bez pliku wracają w
ostrzeżeniach, bo CSV nie ma gdzie pomieścić zastrzeżenia (D-023).
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
from ksef_mcp.archive import InvoiceArchive, digest_of
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
from ksef_mcp.period_cache import PeriodCache
from ksef_mcp.statement import (
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
    currency_warning,
    internal_root_conflict,
    now_utc,
    prepare_working_directory,
    rendered,
    rows_for,
    statement_file_name,
    unverifiable_warning,
    verification_code,
    write_statement,
)
from tests.conftest import an_allowance
from tests.support.synthetic import (
    SELLER_NIP,
    synthetic_body,
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
    """Liczy, ile z dwudziestu zapytań na godzinę naprawdę poszło do KSeF-u."""

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
    # Notacja wykładnicza i zaokrąglenie to dwa sposoby na rozjazd z Aplikacją
    # Podatnika, których nikt nie przypisałby do generatora CSV.
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
    # Data trwałego zapisu wrzuciłaby fakturę do miesiąca, w którym KSeF
    # skończył ją przetwarzać, a nie do tego, do którego należy.
    assert SEPTEMBER.queried.date_type is DateType.ISSUE


def test_the_window_ends_on_the_last_day_of_the_month() -> None:
    # Okno zestawienia ma obejmować dokładnie miesiąc, o który zapytano.
    # Sprawdzenie „ma koniec" byłoby od GH-84 tautologią — koniec jest wymagany
    # przez typ — więc test pilnuje tego, co nadal może się zepsuć: którego dnia
    # okno się kończy.
    assert SEPTEMBER.queried.date_to.date() == date(2026, 9, 30)


def test_the_default_clock_reads_utc() -> None:
    assert now_utc().tzinfo is UTC


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
    """Indeks bywa starszy niż katalog, który opisuje — pełny odczyt zostaje siatką."""
    code = verification_code(invoice=synthetic_metadata(1), archive=archived, digests={})

    assert code is not None and code.content_hash == digest_of(synthetic_body(1))


def test_an_unreadable_index_does_not_refuse_the_statement(archived: InvoiceArchive) -> None:
    """Indeks jest skrótem tego modułu, a nie jego źródłem prawdy — są nim pliki."""
    archived.index_path.write_text(
        json.dumps({"schema_version": 99, "entries": []}), encoding="utf-8"
    )

    assert archived_digests(archived) == {}


def test_an_invoice_outside_the_archive_gets_no_code(archived: InvoiceArchive) -> None:
    """Obecność treści sprawdza dysk, nigdy indeks: kod jest twierdzeniem o bajtach."""
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
    # Pusta komórka wyglądałaby jak faktura, której skrótu nie policzono.
    assert StatementRow(invoice=synthetic_metadata(1), code=None).cells[-1] == NO_ARCHIVED_BODY


def test_a_row_names_the_currency_its_amounts_are_in(archived: InvoiceArchive) -> None:
    # Bez tego miesiąc z fakturą w euro obok złotówkowej sumował się w jedną
    # liczbę, która nie znaczyła nic (#63).
    row = rows_for(invoices=(synthetic_metadata(1),), archive=archived)[0]

    assert row.cells[COLUMNS.index("Waluta")] == "PLN"


def test_the_file_opens_with_a_header_of_every_column() -> None:
    first = rendered(()).splitlines()[0]

    assert first.lstrip("﻿") == ";".join(COLUMNS)


def test_the_file_opens_with_a_byte_order_mark() -> None:
    # Bez niego polski Excel czyta UTF-8 jako stronę kodową i nazwy
    # kontrahentów przychodzą rozsypane.
    assert rendered(()).startswith("﻿")


def test_the_file_name_says_what_the_attachment_is() -> None:
    assert statement_file_name(period=SEPTEMBER, nip=NIP) == "zestawienie-2026-09-1234567890.csv"


def test_the_file_name_carries_the_warning_about_a_shortened_period() -> None:
    # Ostrzeżenie z odpowiedzi narzędzia nie dojeżdża do księgowej — plik tak.
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
    # Ktoś mógł wskazać katalog domowy albo współdzielony; ciche zacieśnienie
    # uprawnień to zmiana, o którą nie prosił.
    existing = tmp_path / "wspolny"
    existing.mkdir(mode=0o755)

    prepared = prepare_working_directory(existing)

    assert prepared.warnings[0].startswith("Katalog roboczy istniał wcześniej")


def test_a_directory_already_0700_raises_no_caveat(working: Path) -> None:
    assert prepare_working_directory(working).warnings == ()


def test_a_cloud_synced_path_is_named_in_as_many_words(tmp_path: Path) -> None:
    prepared = prepare_working_directory(tmp_path / "OneDrive" / "ksef")

    assert "onedrive" in prepared.warnings[0]


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
    # Produktem tego narzędzia jest liczba wysyłana księgowej, a suma z części
    # miesiąca wygląda tak samo jak pełna — ostrzeżenie obok bywa czytane po
    # decyzji albo wcale.
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
    # Kolumny waluty nie ma (rozstrzygnięte w #41), więc faktura w EUR i w PLN
    # dają w kolumnie Brutto liczby nie do odróżnienia.
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


def test_a_correction_in_the_period_cautions_about_the_gross_total(
    archived: InvoiceArchive,
) -> None:
    # Korekta niesie różnicę wobec faktury korygowanej, a nie tę fakturę na
    # nowo (schemat MF, P_15), więc suma po wszystkich wierszach jest sumą
    # dokumentów, nie zobowiązania.
    rows = rows_for(
        invoices=(
            synthetic_metadata(1),
            synthetic_metadata(2, document_type=DocumentType.KOR),
        ),
        archive=archived,
    )

    assert correction_warning(rows)[0].startswith("Okres zawiera faktury korygujące (1 z 2")


def test_a_correction_caveat_names_which_row_it_means(archived: InvoiceArchive) -> None:
    # Bez numeru czytelnik szuka korekty wśród stu trzydziestu wierszy, a
    # ostrzeżenie, które każe szukać, jest ostrzeżeniem pomijanym.
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
    # FA, zaliczkowa, rozliczeniowa, PEF i FA_RR mają własny rodzaj korygujący.
    # Sprawdzenie napisane pod samo `kor` przeszłoby testy i przepuściło cztery.
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
    # Tabela rodzajów już rosła. Milczenie o dokumencie, którego ta wersja nie
    # umie zaklasyfikować, przepuściłoby przyszły rodzaj korygujący.
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

    assert any("korygujące" in one for one in result.warnings)


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
    # Kontrahentem w każdej z ośmiu kolumn jest sprzedawca, więc podmiot
    # pytający jest nabywcą — jedno zapytanie zamiast czterech.
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
    # FA(2)/FA(3) to dane osobowe kontrahenta i nie przekraczają tej granicy
    # (D-011) — do CSV trafia skrót pliku, nigdy jego zawartość.
    assert "<Faktura" not in written


def test_the_file_carries_no_local_paths(written: str, archived: InvoiceArchive) -> None:
    # Ścieżka po przesłaniu jest bezużyteczna albo myląca; niesie ją odpowiedź
    # narzędzia, nie załącznik (D-011).
    assert str(archived.invoice_directory) not in written


def test_deleting_the_working_directory_touches_neither_archive_nor_cache(
    statement: Statement, archived: InvoiceArchive, cache: PeriodCache, working: Path
) -> None:
    # Sedno D-032: katalog roboczy jest produktem, magazyn jest wewnętrzny.
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
