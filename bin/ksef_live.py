#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Uruchom testy `ksef_live` na środowisku testowym KSeF — lokalnie i w CI.

Testy na żywo czytają konfigurację z prawdziwej ścieżki po onboardingu. Na
maszynie, na której podmiot jest skonfigurowany na produkcję, pomijają się
wszystkie — i słusznie, bo test nigdy nie idzie na produkcję. Ten skrypt daje
im osobną, wyłącznie testową konfigurację, nie ruszając tej produkcyjnej:

- konfiguracja z `environment: "test"` wpisanym na sztywno trafia do
  `.tmp/ksef-live/`, a `XDG_DATA_HOME` wskazuje tam tylko na czas biegu;
- token testowy idzie ścieżką awaryjną `KSEF_TOKEN` (D-004), więc wpis
  w keyringu — kluczowany samym NIP-em, wspólnym dla TEST i produkcji —
  nie jest ani czytany, ani nadpisywany.

Poświadczenia pochodzą ze zmiennych środowiskowych albo z nieśledzonego
pliku `ksef.secrets.env` w katalogu repozytorium (zmienne mają pierwszeństwo).
Konwencja jak w bl-zebra: `*.secrets.env` jest ignorowany, a obok leży
commitowany szablon `ksef.secrets.env.example`:

    cp ksef.secrets.env.example ksef.secrets.env

    KSEF_LIVE_TEST_NIP=<NIP podmiotu na środowisku testowym>
    KSEF_LIVE_TEST_TOKEN=<token wygenerowany na środowisku testowym>

Wartość `missing-secret` z szablonu liczy się jako brak.

Nazwy celowo nie brzmią `KSEF_TOKEN`: serwer czyta tę zmienną sam, a token
testowy nie ma prawa trafić do zwykłego uruchomienia. Skrypt nigdy nie
wypisuje NIP-u ani tokenu. Nie ma też żadnej zmiennej wybierającej
środowisko (CLAUDE.md) — `test` jest stałą w kodzie.

Uruchamiaj: `make test-live` albo `bin/ksef_live.py [argumenty pytest]`.
Tego samego skryptu używa workflow `ksef-live.yml`, z sekretami środowiska
`ksef-test` podanymi jako zmienne.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

SECRETS_FILE_NAME = "ksef.secrets.env"

# Wartość zastępcza z szablonu: skopiowany, ale niewypełniony plik to brak
# poświadczeń, a nie „token” wysłany do KSeF.
PLACEHOLDER = "missing-secret"

LIVE_DIRECTORY = Path(".tmp") / "ksef-live"

NIP_KEY = "KSEF_LIVE_TEST_NIP"

TOKEN_KEY = "KSEF_LIVE_TEST_TOKEN"

# Stała, nie parametr: jedyny bezpiecznik przed produkcją to brak jakiejkolwiek
# drogi, którą dałoby się ją tu wybrać.
ENVIRONMENT = "test"

CONFIGURATION_SCHEMA_VERSION = 1

PYTEST_COMMAND = ("uv", "run", "pytest", "-m", "ksef_live", "--no-cov", "-rs")


class CredentialsMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    nip: str
    token: str

    def __repr__(self) -> str:
        # Ani NIP, ani token nie mogą trafić do tracebacku ani logu CI.
        return "Credentials(nip=<ukryty>, token=<ukryty>)"


def parsed_secrets(text: str) -> dict[str, str]:
    """Pary KLUCZ=WARTOŚĆ w formacie, który rozumie też docker-compose i direnv."""
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        pairs[key] = value.strip().strip("'\"")
    return pairs


def secrets_file(root: Path) -> Path:
    return root / SECRETS_FILE_NAME


def resolved_credentials(*, environment: Mapping[str, str], root: Path) -> Credentials:
    source = secrets_file(root)
    stored = parsed_secrets(source.read_text(encoding="utf-8")) if source.is_file() else {}
    nip = environment.get(NIP_KEY) or stored.get(NIP_KEY)
    token = environment.get(TOKEN_KEY) or stored.get(TOKEN_KEY)
    missing = [
        name
        for name, value in ((NIP_KEY, nip), (TOKEN_KEY, token))
        if not value or value == PLACEHOLDER
    ]
    if missing:
        raise CredentialsMissing(
            f"Brak {', '.join(missing)}. Ustaw zmienne środowiskowe albo uzupełnij "
            f"nieśledzony {source} (`cp {SECRETS_FILE_NAME}.example {SECRETS_FILE_NAME}`). "
            "Wartości pochodzą ze środowiska testowego KSeF, nigdy z produkcji."
        )
    return Credentials(nip=nip, token=token)


def configuration_document(*, nip: str, working_directory: Path) -> dict[str, object]:
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "nip": nip,
        "environment": ENVIRONMENT,
        "keyring_backend": "KSEF_TOKEN",
        "invoice_directory": str(working_directory),
    }


def prepared_live_directory(*, credentials: Credentials, root: Path) -> Path:
    """Zapisz konfigurację testową i zwróć katalog, na który wskaże XDG_DATA_HOME."""
    live = root / LIVE_DIRECTORY
    data_home = live / "dane"
    configuration = data_home / "ksef-mcp" / "configuration.json"
    working = live / "robocze"
    configuration.parent.mkdir(parents=True, exist_ok=True)
    working.mkdir(parents=True, exist_ok=True)
    document = configuration_document(nip=credentials.nip, working_directory=working)
    configuration.write_text(json.dumps(document, indent=2), encoding="utf-8")
    # Plik nosi NIP podatnika.
    configuration.chmod(0o600)
    return live


def run_environment(
    *,
    base: Mapping[str, str],
    credentials: Credentials,
    live: Path,
) -> dict[str, str]:
    return {
        **base,
        "XDG_DATA_HOME": str(live / "dane"),
        "XDG_CACHE_HOME": str(live / "pamiec"),
        "KSEF_TOKEN": credentials.token,
    }


def main(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] = os.environ,
    root: Path = REPOSITORY_ROOT,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    try:
        credentials = resolved_credentials(environment=environment, root=root)
    except CredentialsMissing as missing:
        print(f"ksef_live: {missing}", file=sys.stderr)
        return 2
    live = prepared_live_directory(credentials=credentials, root=root)
    completed = runner(
        [*PYTEST_COMMAND, *arguments],
        cwd=root,
        env=run_environment(base=environment, credentials=credentials, live=live),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
