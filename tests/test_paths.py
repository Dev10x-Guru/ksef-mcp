"""Czy jeden podatnik ma jedno archiwum i czy `--nip` nie wyprowadzi poza nie.

Dwa niezmienniki, oba wynikające z tego, że NIP jest jednocześnie kluczem w
keyringu i członem ścieżki. Zapis z myślnikami, ze spacjami i z prefiksem `PL`
to ten sam podatnik, więc musi trafić do tego samego katalogu i tego samego wpisu
poświadczeń (GH-111). A ponieważ ten człon ścieżki pochodzi od użytkownika i
prowadzi do `purge`, który kasuje pliki, alfabet musi być zamknięty na dziesięć
cyfr — żadne `..` ani `/` nie ma prawa przez niego przejść (GH-112).
"""

from pathlib import Path

import pytest
from platformdirs import user_cache_path, user_data_path

from ksef_mcp import paths
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.paths import Nip, NipRejected, SubjectScope

NIP = "1234563218"


@pytest.mark.parametrize(
    "spelling",
    ["1234563218", "123-456-32-18", "123 456 32 18", " 1234563218 ", "PL1234563218"],
)
def test_kazdy_zapis_jednego_numeru_daje_ten_sam_nip(spelling: str) -> None:
    assert Nip.parsed(spelling).value == NIP


def test_ten_sam_podatnik_zapisany_dwojako_ma_jedno_archiwum() -> None:
    # Sedno GH-111: dwa zapisy dawały po cichu dwa katalogi i dwa wpisy w
    # keyringu, bez błędu i bez ostrzeżenia.
    grouped = SubjectScope.parsed(nip="123-456-32-18", environment=KsefEnvironment.TEST)
    plain = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)

    assert grouped == plain


@pytest.mark.parametrize(
    "rejected",
    [
        "../../..",
        "../1234563218",
        "1234563218/../../etc",
        "123456321",
        "12345632180",
        "12345632ab",
        "",
        "PL",
        "١٢٣٤٥٦٣٢١٨",
    ],
)
def test_zapis_ktory_nie_jest_nipem_jest_odrzucany(rejected: str) -> None:
    with pytest.raises(NipRejected):
        Nip.parsed(rejected)


def test_odrzucenie_nie_powtarza_odrzuconej_wartosci() -> None:
    # NIP to dane osobowe, a wyjątek wędruje do śladów stosu i ładunków błędów
    # MCP (D-011). Komunikat mówi, czego oczekiwano, nie co dostał.
    with pytest.raises(NipRejected) as refusal:
        Nip.parsed("987-654-32-10-XX")

    assert "9876543210" not in str(refusal.value)


def test_nip_wraca_do_zapisu_pod_ktorym_jest_trzymany() -> None:
    assert str(Nip.parsed("123-456-32-18")) == NIP


def test_kazde_srodowisko_ma_wlasny_katalog(tmp_path: Path) -> None:
    # Punkt kontynuacji z testowego, użyty na produkcji, uznałby za pobrany
    # okres, którego nikt nie pobrał.
    test = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)
    production = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.PRODUCTION)

    assert test.data_root(override=tmp_path) != production.data_root(override=tmp_path)


def test_kazdy_podmiot_ma_wlasny_katalog(tmp_path: Path) -> None:
    # Wspólny katalog to główny sposób, w jaki biuro rachunkowe miesza klientów
    # (D-034).
    client = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)
    neighbour = SubjectScope.parsed(nip="9876543210", environment=KsefEnvironment.TEST)

    assert client.data_root(override=tmp_path) != neighbour.data_root(override=tmp_path)


def test_korzen_danych_i_korzen_cache_to_dwa_rozne_miejsca(tmp_path: Path) -> None:
    scope = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)

    assert scope.data_root(override=tmp_path / "dane") != scope.cache_root(
        override=tmp_path / "cache"
    )


def test_uklad_katalogu_podmiotu_to_podmiot_potem_srodowisko(tmp_path: Path) -> None:
    scope = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.DEMO)

    assert scope.data_root(override=tmp_path) == tmp_path / "subjects" / NIP / "demo"


@pytest.fixture
def scope() -> SubjectScope:
    return SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)


def test_katalog_spod_starego_zapisu_jest_zgloszony(scope: SubjectScope, tmp_path: Path) -> None:
    (tmp_path / "subjects" / "123-456-32-18" / "test").mkdir(parents=True)

    assert scope.unnormalised_twins(override=tmp_path) == (tmp_path / "subjects" / "123-456-32-18",)


def test_katalog_spod_starego_zapisu_nie_jest_ruszany(scope: SubjectScope, tmp_path: Path) -> None:
    # Decyzja o wstecznej zgodności: wykrycie, nie migracja. W tych katalogach
    # leżą faktury z danymi kontrahentów i nie przenosimy ich bez pytania.
    stale = tmp_path / "subjects" / "123-456-32-18" / "test"
    stale.mkdir(parents=True)

    scope.unnormalised_twins(override=tmp_path)

    assert stale.is_dir()


def test_wlasny_katalog_nie_jest_zglaszany_jako_obcy(scope: SubjectScope, tmp_path: Path) -> None:
    scope.data_root(override=tmp_path).mkdir(parents=True)

    assert scope.unnormalised_twins(override=tmp_path) == ()


def test_katalog_innego_podatnika_nie_jest_zglaszany(scope: SubjectScope, tmp_path: Path) -> None:
    (tmp_path / "subjects" / "9876543210" / "test").mkdir(parents=True)

    assert scope.unnormalised_twins(override=tmp_path) == ()


def test_smiec_w_katalogu_podmiotow_nie_wywraca_sprawdzenia(
    scope: SubjectScope, tmp_path: Path
) -> None:
    (tmp_path / "subjects").mkdir(parents=True)
    (tmp_path / "subjects" / "nie-jest-nipem").mkdir()
    (tmp_path / "subjects" / "1234563218.txt").write_text("", encoding="utf-8")

    assert scope.unnormalised_twins(override=tmp_path) == ()


def test_brak_katalogu_podmiotow_to_brak_znalezisk(scope: SubjectScope, tmp_path: Path) -> None:
    assert scope.unnormalised_twins(override=tmp_path) == ()


def test_bez_podmiany_korzenie_sa_tymi_z_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Autouse'owy fixture podmienia oba korzenie każdemu innemu testowi, żeby
    # suite nie pisała do katalogu danych osoby, która ją uruchomiła. Ten jeden
    # test jest o samym powiązaniu, więc wstawia prawdziwe funkcje z powrotem.
    monkeypatch.setattr(paths, "user_data_path", user_data_path)
    monkeypatch.setattr(paths, "user_cache_path", user_cache_path)
    scope = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)
    tail = Path("subjects") / NIP / "test"

    assert scope.data_root() == user_data_path(appname=SERVER_NAME) / tail
    assert scope.cache_root() == user_cache_path(appname=SERVER_NAME) / tail
