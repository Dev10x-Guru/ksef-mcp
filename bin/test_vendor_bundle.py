"""Testy `bin/vendor_bundle.py` — egzekutora noty licencyjnej.

Sprawdzane są obie strony kontraktu: co skrypt przepuszcza i co odrzuca.
Drzewo zastępcze zakładane jest w `tmp_path`, więc żaden test nie zależy
od tego, jaka wersja generatora leży akurat w repozytorium — z jednym
wyjątkiem na końcu, który celowo patrzy na prawdziwy artefakt, bo to
właśnie ten plik ma być pilnowany.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "vendor_bundle.py"

REPOSITORY_ROOT = SCRIPT.parent.parent


def load_module() -> object:
    specification = importlib.util.spec_from_file_location("vendor_bundle", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    # Zarejestrowany przed wykonaniem: @dataclass rozwiązuje adnotacje przez
    # sys.modules[cls.__module__], pusty dla modułu ładowanego ze ścieżki.
    sys.modules["vendor_bundle"] = module
    specification.loader.exec_module(module)
    return module


vendor_bundle = load_module()


BUNDLE = b"// zastepczy generator\n"

NOTE = """\
# Nota licencyjna — artefakt obcy

| | |
|---|---|
| Pobrano | 2026-09-14 |
| Rozmiar | {byte_count} bajtów |
| SHA-256 | `{digest}` |
"""


def note_for(content: bytes) -> str:
    return NOTE.format(byte_count=len(content), digest=hashlib.sha256(content).hexdigest())


@pytest.fixture
def vendor(tmp_path: Path) -> Path:
    directory = tmp_path / "src" / "ksef_mcp" / "rendering" / "vendor"
    directory.mkdir(parents=True)
    (directory / "ksef-fe-invoice-converter.1.1.39.js").write_bytes(BUNDLE)
    (directory / "LICENCJA-MF.md").write_text(note_for(BUNDLE), encoding="utf-8")
    return tmp_path


def refusal(root: Path) -> str:
    with pytest.raises(vendor_bundle.BundleRefused) as raised:
        vendor_bundle.verify(root=root)
    return str(raised.value)


def test_a_bundle_matching_its_note_is_accepted(vendor: Path) -> None:
    assert "zgodne" in vendor_bundle.verify(root=vendor)


def test_the_accepted_report_names_the_artefact_it_checked(vendor: Path) -> None:
    assert "ksef-fe-invoice-converter.1.1.39.js" in vendor_bundle.verify(root=vendor)


def test_a_transfer_cut_in_half_is_named_as_a_short_file(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/ksef-fe-invoice-converter.1.1.39.js").write_bytes(
        BUNDLE[:5]
    )

    assert "bajtów, a LICENCJA-MF.md zapowiada" in refusal(vendor)


def test_a_short_file_is_told_how_to_be_fetched_again(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/ksef-fe-invoice-converter.1.1.39.js").write_bytes(
        BUNDLE[:5]
    )

    assert "curl -C -" in refusal(vendor)


def test_content_swapped_for_something_of_equal_length_is_refused(vendor: Path) -> None:
    # Ta sama długość, inna zawartość: kontrola rozmiaru przepuszcza, więc
    # tylko skrót ma tu cokolwiek do powiedzenia.
    swapped = b"x" * len(BUNDLE)
    (vendor / "src/ksef_mcp/rendering/vendor/ksef-fe-invoice-converter.1.1.39.js").write_bytes(
        swapped
    )

    assert "skrót SHA-256" in refusal(vendor)


def test_a_missing_note_is_refused_rather_than_read_as_nothing_to_check(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").unlink()

    assert "Brak noty licencyjnej" in refusal(vendor)


def test_a_note_without_a_digest_row_is_refused(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").write_text(
        "# Nota\n\n| Rozmiar | 23 bajtów |\n", encoding="utf-8"
    )

    assert "SHA-256" in refusal(vendor)


def test_a_note_without_a_size_row_is_refused(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").write_text(
        f"# Nota\n\n| SHA-256 | `{hashlib.sha256(BUNDLE).hexdigest()}` |\n", encoding="utf-8"
    )

    assert "Rozmiar" in refusal(vendor)


def test_a_digest_quoted_in_prose_is_not_mistaken_for_the_declaration(vendor: Path) -> None:
    # Nota tłumaczy sama siebie zdaniami; deklaracją jest wiersz tabeli, nie
    # wzmianka. Bez zakotwiczenia w całej linii zdanie poniżej wystarczyłoby.
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").write_text(
        f"Skrót SHA-256 `{hashlib.sha256(BUNDLE).hexdigest()}` zapisano w tabeli.\n",
        encoding="utf-8",
    )

    assert "wiersza" in refusal(vendor)


def test_a_missing_bundle_is_refused(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/ksef-fe-invoice-converter.1.1.39.js").unlink()

    assert "nie ma pliku pasującego" in refusal(vendor)


def test_a_half_finished_version_swap_is_refused_rather_than_guessed(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/ksef-fe-invoice-converter.1.1.40.js").write_bytes(
        BUNDLE
    )

    assert "więcej niż jeden generator" in refusal(vendor)


def test_a_size_written_with_digit_groups_is_read_as_one_number(vendor: Path) -> None:
    padded = b"y" * 1234
    (vendor / "src/ksef_mcp/rendering/vendor/ksef-fe-invoice-converter.1.1.39.js").write_bytes(
        padded
    )
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").write_text(
        NOTE.format(byte_count="1 234", digest=hashlib.sha256(padded).hexdigest()),
        encoding="utf-8",
    )

    assert "zgodne" in vendor_bundle.verify(root=vendor)


def test_the_artefact_in_this_repository_matches_its_own_note() -> None:
    assert "zgodne" in vendor_bundle.verify(root=REPOSITORY_ROOT)


def test_the_command_line_reports_a_refusal_on_stderr(
    vendor: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").unlink()
    vendor_bundle.main(["--root", str(vendor)])

    assert "Bundel odrzucony" in capsys.readouterr().err


def test_the_command_line_refuses_with_a_non_zero_exit(vendor: Path) -> None:
    (vendor / "src/ksef_mcp/rendering/vendor/LICENCJA-MF.md").unlink()

    assert vendor_bundle.main(["--root", str(vendor)]) == 1


def test_the_command_line_succeeds_on_an_intact_bundle(vendor: Path) -> None:
    assert vendor_bundle.main(["--root", str(vendor)]) == 0
