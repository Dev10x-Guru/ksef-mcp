"""Writing, removing and describing the token — never showing it."""

from __future__ import annotations

from typing import Final

from ksef_mcp import messages
from ksef_mcp.cli.console import Console, ask_secret_required
from ksef_mcp.cli.exits import EXIT_NO_TOKEN, EXIT_OK
from ksef_mcp.storage import token_store

TOKEN_PORTAL_URL: Final[str] = "https://ap.ksef.mf.gov.pl/web/tokens/generate-token"


def explain_token_step(console: Console) -> None:
    console.write("")
    console.write("Token wygenerujesz pod adresem:")
    console.write(f"  {TOKEN_PORTAL_URL}")
    console.write("Wymaga wcześniejszego uwierzytelnienia podpisem")
    console.write("kwalifikowanym albo Profilem Zaufanym — tego kroku nie da")
    console.write("się zautomatyzować i nie udaję, że przeprowadzę przez całość.")
    console.write("Do odczytu faktur wystarczy uprawnienie InvoiceRead;")
    console.write("uprawnienia tokenu są niezmienne, więc nie bierz zapasowych.")
    console.write("Token zobaczysz tylko raz.")


def run_token_set(console: Console, *, nip: str) -> int:
    explain_token_step(console)
    token = ask_secret_required(console, prompt="Wklej token KSeF (bez echa)")
    stored = token_store.store_token(nip=nip, token=token)
    console.write(
        f"Token dla {nip} zapisany i zweryfikowany: {stored.length} znaków, "
        f"końcówka …{stored.suffix}"
    )
    return EXIT_OK


def warn_about_the_export(console: Console) -> None:
    console.write(
        f"UWAGA: zmienna {token_store.FALLBACK_ENVIRONMENT_VARIABLE} jest nadal "
        "ustawiona i ma pierwszeństwo, więc token wciąż działa."
    )
    console.write(f"Wycofaj go z sesji: unset {token_store.FALLBACK_ENVIRONMENT_VARIABLE}")


def run_token_delete(console: Console, *, nip: str) -> int:
    removal = token_store.delete_token(nip=nip)
    if removal.removed_from_keyring:
        console.write(f"Token dla {nip} usunięty z keyringu.")
    else:
        console.write(f"W keyringu nie było tokenu dla {nip}.")
    if removal.still_exported:
        warn_about_the_export(console)
    return EXIT_OK if removal.removed_from_keyring else EXIT_NO_TOKEN


def run_token_status(console: Console, *, nip: str) -> int:
    stored = token_store.read_token(nip=nip)
    if stored is None:
        console.write(f"Brak tokenu dla {nip}. Zapisz go: ksef-mcp token set --nip {nip}")
        return EXIT_NO_TOKEN
    described = token_store.fingerprint(stored.value)
    console.write(
        f"Token dla {nip} pochodzi z {messages.describe_source(stored.source)}: "
        f"{described.length} znaków, końcówka …{described.suffix}. "
        "Wartości nie pokazuję."
    )
    return EXIT_OK
