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

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from conftest import an_allowance
from ksef_mcp.allowance import Allowance
from ksef_mcp.archive import InvoiceArchive, digest_of
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    DateType,
    InvoiceDirection,
    KsefLimits,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
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
    completeness_warning,
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
from synthetic import (
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
    asked: list[InvoiceDirection] = field(default_factory=list)
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
        direction: InvoiceDirection,
        page_offset: int = 0,
    ) -> MetadataPage:
        self.asked.append(direction)
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
def test_kwota_trafia_do_komorki_bez_straty_grosza(value: Decimal, expected: str) -> None:
    # Notacja wykładnicza i zaokrąglenie to dwa sposoby na rozjazd z Aplikacją
    # Podatnika, których nikt nie przypisałby do generatora CSV.
    assert amount(value) == expected


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [("2026-01", (2026, 1)), ("2026-12", (2026, 12)), ("1999-08", (1999, 8))],
)
def test_okres_czyta_sie_jako_miesiac(spelling: str, expected: tuple[int, int]) -> None:
    parsed = AccountingPeriod.parsed(spelling)

    assert (parsed.year, parsed.month) == expected


@pytest.mark.parametrize("spelling", ["2026-13", "2026-00", "sierpień", "2026-8", "2026"])
def test_okres_nie_do_odczytania_konczy_sie_wyjatkiem(spelling: str) -> None:
    with pytest.raises(UnreadablePeriod, match="RRRR-MM"):
        AccountingPeriod.parsed(spelling)


def test_okres_wraca_do_zapisu_ktory_go_zrodzil() -> None:
    assert str(AccountingPeriod.parsed("2026-08")) == "2026-08"


def test_okno_obejmuje_caly_miesiac() -> None:
    window = AccountingPeriod(year=2026, month=2).queried

    assert (window.date_from, window.date_to) == (
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, 23, 59, 59, 999999, tzinfo=UTC),
    )


def test_okno_pyta_o_date_wystawienia() -> None:
    # Data trwałego zapisu wrzuciłaby fakturę do miesiąca, w którym KSeF
    # skończył ją przetwarzać, a nie do tego, do którego należy.
    assert SEPTEMBER.queried.date_type is DateType.ISSUE


def test_okno_konczy_sie_na_ostatnim_dniu_miesiaca() -> None:
    # Okno zestawienia ma obejmować dokładnie miesiąc, o który zapytano.
    # Sprawdzenie „ma koniec" byłoby od GH-84 tautologią — koniec jest wymagany
    # przez typ — więc test pilnuje tego, co nadal może się zepsuć: którego dnia
    # okno się kończy.
    assert SEPTEMBER.queried.date_to.date() == date(2026, 9, 30)


def test_domyslny_zegar_czyta_utc() -> None:
    assert now_utc().tzinfo is UTC


def test_kod_pierwszy_sklada_sie_z_trzech_czlonow() -> None:
    code = VerificationCode(
        seller_nip=SELLER_NIP,
        issue_date=date(2026, 9, 1),
        content_hash="abc123",
    )

    assert str(code) == f"{SELLER_NIP}-20260901-abc123"


def test_kod_pierwszy_bierze_skrot_z_pliku_archiwum(archived: InvoiceArchive) -> None:
    code = verification_code(invoice=synthetic_metadata(1), archive=archived)

    assert code is not None and code.content_hash == digest_of(synthetic_body(1))


def test_faktura_spoza_archiwum_nie_dostaje_kodu(archived: InvoiceArchive) -> None:
    assert verification_code(invoice=synthetic_metadata(2), archive=archived) is None


def test_wiersz_niesie_osiem_kolumn_walute_i_kod(archived: InvoiceArchive) -> None:
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


def test_sprzedawca_bez_nazwy_daje_puste_pole(archived: InvoiceArchive) -> None:
    row = StatementRow(invoice=synthetic_metadata(1, seller_name=None), code=None)

    assert row.cells[4] == ""


def test_pozycja_bez_pliku_mowi_o_tym_wprost() -> None:
    # Pusta komórka wyglądałaby jak faktura, której skrótu nie policzono.
    assert StatementRow(invoice=synthetic_metadata(1), code=None).cells[-1] == NO_ARCHIVED_BODY


def test_wiersz_nazywa_walute_kwot(archived: InvoiceArchive) -> None:
    # Bez tego miesiąc z fakturą w euro obok złotówkowej sumował się w jedną
    # liczbę, która nie znaczyła nic (#63).
    row = rows_for(invoices=(synthetic_metadata(1),), archive=archived)[0]

    assert row.cells[COLUMNS.index("Waluta")] == "PLN"


def test_plik_zaczyna_sie_naglowkiem_wszystkich_kolumn() -> None:
    first = rendered(()).splitlines()[0]

    assert first.lstrip("﻿") == ";".join(COLUMNS)


def test_plik_otwiera_znacznik_kolejnosci_bajtow() -> None:
    # Bez niego polski Excel czyta UTF-8 jako stronę kodową i nazwy
    # kontrahentów przychodzą rozsypane.
    assert rendered(()).startswith("﻿")


def test_nazwa_pliku_mowi_czym_jest_zalacznik() -> None:
    assert statement_file_name(period=SEPTEMBER, nip=NIP) == "zestawienie-2026-09-1234567890.csv"


@pytest.mark.parametrize("root", ["dane", "cache"])
def test_katalog_wewnatrz_magazynu_wewnetrznego_jest_odrzucany(tmp_path: Path, root: str) -> None:
    with pytest.raises(WorkingDirectoryRefused, match="D-032"):
        prepare_working_directory(
            tmp_path / root / "zestawienia",
            data_root=tmp_path / "dane",
            cache_root_path=tmp_path / "cache",
        )


def test_sam_korzen_danych_tez_jest_odrzucany(tmp_path: Path) -> None:
    with pytest.raises(WorkingDirectoryRefused, match="D-032"):
        prepare_working_directory(
            tmp_path / "dane",
            data_root=tmp_path / "dane",
            cache_root_path=tmp_path / "cache",
        )


def test_katalog_poza_oboma_korzeniami_nie_powoduje_kolizji(tmp_path: Path) -> None:
    assert (
        internal_root_conflict(
            tmp_path / "robocze",
            data_root=tmp_path / "dane",
            cache_root_path=tmp_path / "cache",
        )
        is None
    )


def test_nowy_katalog_roboczy_powstaje_z_uprawnieniami_0700(working: Path) -> None:
    prepared = prepare_working_directory(working)

    assert (prepared.created, prepared.mode) == (True, 0o700)


def test_istniejacy_katalog_zachowuje_swoje_uprawnienia(tmp_path: Path) -> None:
    # Ktoś mógł wskazać katalog domowy albo współdzielony; ciche zacieśnienie
    # uprawnień to zmiana, o którą nie prosił.
    existing = tmp_path / "wspolny"
    existing.mkdir(mode=0o755)

    prepared = prepare_working_directory(existing)

    assert prepared.warnings[0].startswith("Katalog roboczy istniał wcześniej")


def test_katalog_z_uprawnieniami_0700_nie_wywoluje_ostrzezenia(working: Path) -> None:
    assert prepare_working_directory(working).warnings == ()


def test_sciezka_synchronizowana_do_chmury_jest_nazwana_wprost(tmp_path: Path) -> None:
    prepared = prepare_working_directory(tmp_path / "OneDrive" / "ksef")

    assert "onedrive" in prepared.warnings[0]


def test_katalog_bez_znacznika_chmury_nic_o_niej_nie_mowi() -> None:
    directory = WorkingDirectory(path=Path("/robocze"), created=True, mode=0o700, cloud_marker=None)

    assert directory.warnings == ()


def test_zapis_zostawia_plik_tylko_dla_wlasciciela(
    tmp_path: Path, archived: InvoiceArchive
) -> None:
    path = write_statement(
        rows=rows_for(invoices=(synthetic_metadata(1),), archive=archived),
        path=tmp_path / "zestawienie.csv",
    )

    assert path.stat().st_mode & 0o777 == 0o600


def test_zapis_nie_zostawia_pliku_przejsciowego(tmp_path: Path) -> None:
    write_statement(rows=(), path=tmp_path / "zestawienie.csv")

    assert sorted(one.name for one in tmp_path.iterdir()) == ["zestawienie.csv"]


@pytest.mark.parametrize(
    ("complete", "expected"),
    [(True, 0), (False, 1)],
)
def test_niekompletny_okres_jest_powiedziany_wprost(complete: bool, expected: int) -> None:
    assert len(completeness_warning(complete=complete)) == expected


def test_jedna_waluta_nie_wymaga_ostrzezenia(statement: Statement) -> None:
    assert currency_warning(statement.gross_totals) == ()


def test_kilka_walut_ostrzega_o_kolumnie_brutto(composer: StatementComposer, working: Path) -> None:
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


def test_pozycje_bez_pliku_sa_policzone(archived: InvoiceArchive) -> None:
    rows = rows_for(invoices=(synthetic_metadata(1), synthetic_metadata(2)), archive=archived)

    assert unverifiable_warning(rows)[0].startswith("Bez KOD I: 1 z 2")


def test_komplet_pozycji_z_kodem_nie_ostrzega(archived: InvoiceArchive) -> None:
    assert unverifiable_warning(rows_for(invoices=(synthetic_metadata(1),), archive=archived)) == ()


def test_zestawienie_lezy_w_katalogu_roboczym(statement: Statement, working: Path) -> None:
    assert Path(statement.path).parent == working


def test_zestawienie_liczy_pozycje(statement: Statement) -> None:
    assert statement.row_count == 2


def test_zestawienie_podaje_sume_brutto(statement: Statement) -> None:
    assert statement.gross_totals[0].gross == Decimal("2460.00")


def test_suma_zgadza_sie_z_kolumna_brutto(statement: Statement, written: str) -> None:
    rows = written.lstrip("﻿").strip().splitlines()[1:]
    summed = sum(Decimal(row.split(";")[5].replace(",", ".")) for row in rows)

    assert summed == statement.gross_totals[0].gross


def test_zestawienie_pyta_ksef_tylko_o_role_nabywcy(
    statement: Statement, session: RecordingSession
) -> None:
    # Kontrahentem w każdej z ośmiu kolumn jest sprzedawca, więc podmiot
    # pytający jest nabywcą — jedno zapytanie zamiast czterech.
    assert session.asked == [InvoiceDirection.BUYER]


def test_drugie_zlozenie_tego_samego_miesiaca_nie_kosztuje_zapytania(
    composer: StatementComposer, working: Path, session: RecordingSession
) -> None:
    composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)
    composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert len(session.asked) == 1


def test_zestawienie_z_dysku_mowi_ze_jest_z_dysku(
    composer: StatementComposer, working: Path
) -> None:
    composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    repeated = composer.run(nip=NIP, token=CREDENTIAL, period=SEPTEMBER, directory=working)

    assert repeated.from_cache is True


def test_pierwsze_zlozenie_nie_jest_z_dysku(statement: Statement) -> None:
    assert statement.from_cache is False


def test_zestawienie_stempluje_moment_zapytania(statement: Statement) -> None:
    assert statement.queried_at == ASKED_AT


def test_zestawienie_nazywa_srodowisko_z_ktorym_rozmawialo(statement: Statement) -> None:
    assert statement.environment is KsefEnvironment.TEST


def test_komunikat_niesie_liczbe_sume_i_sciezke(statement: Statement) -> None:
    assert statement.message == (
        f"Zestawienie za 2026-09: 2 faktury, brutto 2460.00 PLN. Plik: {statement.path}"
    )


def test_pusty_miesiac_ma_komunikat_bez_sumy(
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


def test_uciety_okres_wraca_z_ostrzezeniem(
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


def test_plik_nie_niesie_tresci_faktury(written: str) -> None:
    # FA(2)/FA(3) to dane osobowe kontrahenta i nie przekraczają tej granicy
    # (D-011) — do CSV trafia skrót pliku, nigdy jego zawartość.
    assert "<Faktura" not in written


def test_plik_nie_niesie_sciezek_lokalnych(written: str, archived: InvoiceArchive) -> None:
    # Ścieżka po przesłaniu jest bezużyteczna albo myląca; niesie ją odpowiedź
    # narzędzia, nie załącznik (D-011).
    assert str(archived.invoice_directory) not in written


def test_skasowanie_katalogu_roboczego_nie_rusza_archiwum_ani_cache(
    statement: Statement, archived: InvoiceArchive, cache: PeriodCache, working: Path
) -> None:
    # Sedno D-032: katalog roboczy jest produktem, magazyn jest wewnętrzny.
    body = archived.invoice_directory / f"{synthetic_number(1)}.xml"
    remembered = cache.path_for(period=SEPTEMBER.queried, direction=InvoiceDirection.BUYER)

    shutil.rmtree(working)

    assert (body.is_file(), remembered.is_file(), Path(statement.path).exists()) == (
        True,
        True,
        False,
    )


def test_katalog_roboczy_lezy_poza_korzeniem_danych(
    statement: Statement, archived: InvoiceArchive
) -> None:
    assert not Path(statement.path).is_relative_to(archived.root / SUBJECT_DIRECTORY)
