#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Sprawdź, czy zwendorowany bundel MF jest tym, za co się podaje.

`src/ksef_mcp/vendor/LICENCJA-MF.md` od początku zapisuje rozmiar i skrót
SHA-256 generatora Ministerstwa oraz ostrzega, że portal dwukrotnie zerwał
transfer w połowie. Nota była jednak wyłącznie opisem: nic tych dwóch liczb
nie przeliczało, więc obcięty plik przeszedłby przez commit, przez wydanie
i ujawnił się dopiero u podatnika, przy renderowaniu konkretnej faktury.

Podział ról zostaje taki, jaki opisuje nota: **nota jest źródłem prawdy,
ten skrypt egzekutorem**. Aktualizacja generatora (D-027) polega na
podmianie pliku i przepisaniu noty; skrypt nie zna żadnej wartości
z góry i niczego nie proponuje — porównuje to, co leży na dysku, z tym,
co o tym napisano.

Rozmiar sprawdzany jest przed skrótem, bo nazywa awarię, która faktycznie
zachodzi. Niezgodny skrót mówi tylko „to inny plik"; krótszy plik o
zapowiedzianej nazwie to przerwany transfer i taką odpowiedź warto
dostać wprost.

Uruchamiaj: `bin/vendor_bundle.py [--root KATALOG]`. Wywołują go hook
pre-commit, zadanie CI oraz `bin/release.py` jako bramka przed krokiem
nieodwracalnym.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

VENDOR_DIRECTORY = Path("src") / "ksef_mcp" / "vendor"

NOTE_NAME = "LICENCJA-MF.md"

BUNDLE_GLOB = "ksef-fe-invoice-converter.*.js"

# Wiersze tabeli noty. Zakotwiczone w całej linii, żeby zdanie cytujące
# skrót w prozie nie udawało deklaracji.
DECLARED_DIGEST = re.compile(r"^\|\s*SHA-256\s*\|\s*`([0-9a-f]{64})`\s*\|\s*$", re.MULTILINE)

DECLARED_SIZE = re.compile(r"^\|\s*Rozmiar\s*\|\s*([\d ]+?)\s*bajt\w*\s*\|\s*$", re.MULTILINE)


class BundleRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Declaration:
    digest: str
    byte_count: int


def note_path(root: Path) -> Path:
    return root / VENDOR_DIRECTORY / NOTE_NAME


def declaration(text: str) -> Declaration:
    digest = DECLARED_DIGEST.search(text)
    if digest is None:
        raise BundleRefused(
            f"{NOTE_NAME} nie zawiera wiersza `| SHA-256 | \\`…\\` |`. Nota jest "
            "źródłem prawdy o zwendorowanym artefakcie — bez skrótu nie mam czego "
            "egzekwować."
        )
    size = DECLARED_SIZE.search(text)
    if size is None:
        raise BundleRefused(
            f"{NOTE_NAME} nie zawiera wiersza `| Rozmiar | … bajtów |`. Rozmiar "
            "jest tym, po czym poznaje się przerwany transfer, więc jego brak "
            "wyłączyłby najważniejszą z dwóch kontroli."
        )
    return Declaration(
        digest=digest.group(1),
        byte_count=int(size.group(1).replace(" ", "")),
    )


def bundle_path(root: Path) -> Path:
    directory = root / VENDOR_DIRECTORY
    found = sorted(directory.glob(BUNDLE_GLOB))
    if not found:
        raise BundleRefused(
            f"W {directory} nie ma pliku pasującego do `{BUNDLE_GLOB}`. Bez "
            "generatora Ministerstwa renderowanie PDF nie ma czym działać."
        )
    if len(found) > 1:
        # Nazwa pliku niesie wersję i to po niej rozpoznaje się podmianę
        # (D-027). Dwie wersje obok siebie znaczą, że podmiana stanęła w pół
        # kroku, a nota opisuje wtedy tylko jedną z nich.
        names = ", ".join(path.name for path in found)
        raise BundleRefused(
            f"W {directory} leży więcej niż jeden generator: {names}. Nota opisuje "
            "jeden artefakt, więc nie zgaduję, który z nich jest tym wydawanym."
        )
    return found[0]


def verify(*, root: Path) -> str:
    note = note_path(root)
    if not note.is_file():
        raise BundleRefused(f"Brak noty licencyjnej {note} — nie ma z czym porównać bundla.")
    declared = declaration(note.read_text(encoding="utf-8"))
    bundle = bundle_path(root)
    content = bundle.read_bytes()
    if len(content) != declared.byte_count:
        raise BundleRefused(
            f"{bundle.name} ma {len(content)} bajtów, a {NOTE_NAME} zapowiada "
            f"{declared.byte_count}. Portal Ministerstwa zrywał już transfer "
            "w połowie — pobierz plik ponownie z wznawianiem (`curl -C -`)."
        )
    digest = hashlib.sha256(content).hexdigest()
    if digest != declared.digest:
        raise BundleRefused(
            f"{bundle.name} ma skrót SHA-256 {digest}, a {NOTE_NAME} zapowiada "
            f"{declared.digest}. Rozmiar się zgadza, więc to nie jest urwany "
            "transfer, tylko inna zawartość: albo podmieniono artefakt, albo "
            "przepisano notę bez podmiany pliku."
        )
    return f"{bundle.name}: {len(content)} bajtów i skrót zgodne z {NOTE_NAME}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vendor_bundle.py",
        description="Porównuje zwendorowany bundel MF z jego notą licencyjną.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    try:
        print(verify(root=arguments.root))
    except BundleRefused as refusal:
        print(f"Bundel odrzucony: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
