"""Whether one taxpayer has one archive, and whether `--nip` cannot lead
outside it.

Two invariants, both following from the NIP being at once a keyring key and
a path segment. A spelling with dashes, with spaces, or with the `PL`
prefix is the same taxpayer, so it must land in the same directory and the
same credential entry (GH-111). And because this path segment comes from
the user and leads to `purge`, which deletes files, the alphabet must be
closed to ten digits — no `..` and no `/` may ever pass through it (GH-112).
"""

from pathlib import Path

import pytest
from platformdirs import user_cache_path, user_data_path

from ksef_mcp import paths
from ksef_mcp.ksef_port.types import KsefEnvironment
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
    # The heart of GH-111: two spellings silently gave two directories and
    # two keyring entries, with no error and no warning.
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
    # A NIP is personal data, and the exception travels into stack traces
    # and MCP error payloads (D-011). The message says what was expected,
    # not what was received.
    with pytest.raises(NipRejected) as refusal:
        Nip.parsed("987-654-32-10-XX")

    assert "9876543210" not in str(refusal.value)


def test_nip_wraca_do_zapisu_pod_ktorym_jest_trzymany() -> None:
    assert str(Nip.parsed("123-456-32-18")) == NIP


def test_kazde_srodowisko_ma_wlasny_katalog(tmp_path: Path) -> None:
    # A continuation point from TEST, used against PRODUCTION, would treat
    # a period nobody has fetched as already fetched.
    test = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)
    production = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.PRODUCTION)

    assert test.data_root(override=tmp_path) != production.data_root(override=tmp_path)


def test_kazdy_podmiot_ma_wlasny_katalog(tmp_path: Path) -> None:
    # A shared directory is the main way an accounting office mixes up its
    # clients (D-034).
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


@pytest.fixture
def office(tmp_path: Path) -> Path:
    """An accounting office with three clients: one saved correctly, two with the old spelling."""
    holder = tmp_path / "subjects"
    for name in ("1234563218", "123-456-32-18", "PL9876543210"):
        (holder / name / "test").mkdir(parents=True)
    return holder


def test_katalog_spod_starego_zapisu_jest_zgloszony(office: Path, tmp_path: Path) -> None:
    found = paths.unnormalised_subjects(override=tmp_path)

    assert tuple(subject.directory for subject in found) == (
        office / "123-456-32-18",
        office / "PL9876543210",
    )


def test_zgloszenie_nazywa_katalog_docelowy(office: Path, tmp_path: Path) -> None:
    found = paths.unnormalised_subjects(override=tmp_path)

    assert tuple(subject.normalised for subject in found) == (
        office / "1234563218",
        office / "9876543210",
    )


def test_istniejacy_katalog_docelowy_jest_widoczny_w_zgloszeniu(
    office: Path, tmp_path: Path
) -> None:
    # Two directories for one taxpayer sitting side by side is a different
    # situation from a single directory needing a rename: moving the
    # contents over could overwrite files.
    found = paths.unnormalised_subjects(override=tmp_path)

    assert tuple(subject.normalised_exists for subject in found) == (True, False)


def test_katalog_spod_starego_zapisu_nie_jest_ruszany(office: Path, tmp_path: Path) -> None:
    # A backward-compatibility decision: detection, not migration. These
    # directories hold invoices carrying counterparty data, and we do not
    # move them without asking.
    paths.unnormalised_subjects(override=tmp_path)

    assert (office / "123-456-32-18" / "test").is_dir()


def test_znormalizowany_katalog_nie_jest_zglaszany(scope: SubjectScope, tmp_path: Path) -> None:
    scope.data_root(override=tmp_path).mkdir(parents=True)

    assert paths.unnormalised_subjects(override=tmp_path) == ()


def test_smiec_w_katalogu_podmiotow_nie_wywraca_sprawdzenia(tmp_path: Path) -> None:
    (tmp_path / "subjects").mkdir(parents=True)
    (tmp_path / "subjects" / "nie-jest-nipem").mkdir()
    (tmp_path / "subjects" / "1234563218.txt").write_text("", encoding="utf-8")

    assert paths.unnormalised_subjects(override=tmp_path) == ()


def test_brak_katalogu_podmiotow_to_brak_znalezisk(tmp_path: Path) -> None:
    assert paths.unnormalised_subjects(override=tmp_path) == ()


def test_bez_podmiany_korzenie_sa_tymi_z_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    # An autouse fixture swaps both roots for every other test, so the suite
    # never writes into the data directory of whoever ran it. This one test
    # is about that very wiring, so it puts the real functions back.
    monkeypatch.setattr(paths, "user_data_path", user_data_path)
    monkeypatch.setattr(paths, "user_cache_path", user_cache_path)
    scope = SubjectScope.parsed(nip=NIP, environment=KsefEnvironment.TEST)
    tail = Path("subjects") / NIP / "test"

    assert scope.data_root() == user_data_path(appname=SERVER_NAME) / tail
    assert scope.cache_root() == user_cache_path(appname=SERVER_NAME) / tail
