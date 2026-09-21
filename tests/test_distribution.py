"""Co naprawdę jedzie w dystrybucji — sprawdzane na zbudowanym archiwum.

Reszta pakietu ma testy, które uruchamiają kod z drzewa źródłowego. Tu
chodzi o coś, czego z drzewa źródłowego nie widać: renderowanie PDF (D-027)
potrzebuje dwóch plików, które nie są Pythonem — shimu Node i zwendorowanego
generatora Ministerstwa. Do 106 ich obecność w kole zależała wyłącznie od
tego, czego nie wymienia `.gitignore`, a wypadnięcie ich z dystrybucji nie
psuło ani instalacji, ani importu, ani jednego testu. Awaria wychodziła
dopiero u użytkownika.

Dlatego ten plik nie sprawdza manifestu — sprawdza archiwum. Budowany jest
ten sam ciąg, który wykonuje `pypi-publish.yml`: `uv build` wytwarza sdist,
a koło powstaje z niego, więc zasób nieobecny w sdiscie jest nieobecny
i w kole. Build biegnie raz na moduł, bo kosztuje kilkanaście sekund,
a wszystkie asercje czytają to samo archiwum.
"""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

BUILD_TIMEOUT_SECONDS = 600.0

# Ścieżki wewnątrz pakietu importu, bez przedrostka dystrybucji — koło niesie
# je pod `ksef_mcp/`, sdist pod `src/ksef_mcp/`.
REQUIRED_RESOURCES = (
    "rendering/node/render.mjs",
    "rendering/vendor/ksef-fe-invoice-converter.1.1.39.js",
    "rendering/vendor/package.json",
    "rendering/vendor/LICENCJA-MF.md",
)

# Czy bundel w drzewie zgadza się z notą licencyjną, pilnuje `bin/vendor_bundle.py`
# (108). Tu pytanie jest węższe i dotyczy wyłącznie pakowania: czy to, co
# spakowano, to ten sam plik co w drzewie, czy jego skrócona wersja.
BUNDLE_NAME = "rendering/vendor/ksef-fe-invoice-converter.1.1.39.js"


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--out-dir", str(destination)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=True,
    )
    return destination


@pytest.fixture(scope="module")
def wheel(distributions: Path) -> Iterator[zipfile.ZipFile]:
    with zipfile.ZipFile(next(iter(distributions.glob("*.whl")))) as archive:
        yield archive


@pytest.fixture(scope="module")
def wheel_entries(wheel: zipfile.ZipFile) -> frozenset[str]:
    return frozenset(wheel.namelist())


@pytest.fixture(scope="module")
def sdist_entries(distributions: Path) -> frozenset[str]:
    with tarfile.open(next(iter(distributions.glob("*.tar.gz")))) as archive:
        # Pierwszy człon to katalog nazwany wersją dystrybucji; odcięty, żeby
        # asercje nie musiały znać numeru wydania.
        return frozenset(name.partition("/")[2] for name in archive.getnames())


@pytest.mark.parametrize("resource", REQUIRED_RESOURCES)
def test_the_wheel_carries_every_resource_the_renderer_needs(
    resource: str, wheel_entries: frozenset[str]
) -> None:
    assert f"ksef_mcp/{resource}" in wheel_entries


@pytest.mark.parametrize("resource", REQUIRED_RESOURCES)
def test_the_sdist_carries_every_resource_the_renderer_needs(
    resource: str, sdist_entries: frozenset[str]
) -> None:
    assert f"src/ksef_mcp/{resource}" in sdist_entries


def test_the_bundle_in_the_wheel_is_not_a_truncated_transfer(wheel: zipfile.ZipFile) -> None:
    source = REPOSITORY_ROOT / "src" / "ksef_mcp" / BUNDLE_NAME

    assert wheel.getinfo(f"ksef_mcp/{BUNDLE_NAME}").file_size == source.stat().st_size
