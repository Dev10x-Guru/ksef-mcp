"""Configuration before the first run, in the order the decisions actually arise."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ksef_mcp import config, keyring_preflight, messages
from ksef_mcp.cli.console import (
    Console,
    affirmative,
    ask_required,
    ask_secret_required,
    ask_with_default,
)
from ksef_mcp.cli.diagnostics import report_preflight, run_verify
from ksef_mcp.cli.exits import EXIT_OK, EXIT_UNUSABLE_KEYRING
from ksef_mcp.cli.skills import run_skill_install
from ksef_mcp.cli.tokens import explain_token_step
from ksef_mcp.config import Configuration
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.paths import Nip, NipRejected
from ksef_mcp.setup import client
from ksef_mcp.setup.skill import SkillScope
from ksef_mcp.storage import token_store
from ksef_mcp.storage.archive import InvoiceArchive

ENVIRONMENT_ORDER: Final[tuple[KsefEnvironment, ...]] = (
    KsefEnvironment.TEST,
    KsefEnvironment.DEMO,
    KsefEnvironment.PRODUCTION,
)


def ask_nip(console: Console) -> Nip:
    """Settle the spelling here, once, before anything is keyed by it.

    The same number written `123-456-32-18` and `1234563218` used to give the
    taxpayer two archives and two keyring entries, silently (GH-111). The
    onboarding prompt is where a human spelling enters the process, so it is
    where the spelling stops being a variable.
    """
    while True:
        try:
            return Nip.parsed(ask_required(console, prompt="NIP podmiotu"))
        except NipRejected:
            console.write(messages.describe_rejected_nip())


def preferred_backend_index(report: keyring_preflight.KeyringReport) -> int:
    return next(
        index
        for index, backend in enumerate(report.backends, start=1)
        if backend.module == report.preferred
    )


def choose_keyring_backend(console: Console, report: keyring_preflight.KeyringReport) -> str:
    # Deliberate choice rather than the library's priority order: an unrelated
    # package installing its own backend would otherwise change the winner, and
    # a token written earlier would stop being visible while still existing.
    if len(report.backends) == 1:
        return report.backends[0].module
    default = preferred_backend_index(report)
    while True:
        answer = ask_with_default(
            console,
            prompt="Wybierz magazyn na token (numer)",
            default=str(default),
        )
        if answer.isdigit() and 1 <= int(answer) <= len(report.backends):
            return report.backends[int(answer) - 1].module
        console.write(f"Podaj numer od 1 do {len(report.backends)}.")


def resolve_environment(answer: str) -> KsefEnvironment | None:
    """A number from the list, or the name spelled out — both keep working."""
    if answer.isdigit() and 1 <= int(answer) <= len(ENVIRONMENT_ORDER):
        return ENVIRONMENT_ORDER[int(answer) - 1]
    try:
        return KsefEnvironment(answer)
    except ValueError:
        return None


def choose_environment(console: Console) -> KsefEnvironment:
    # Numbered rather than retyped: the names differ by a few characters and a
    # typo here points the whole install at the wrong registry. The default is
    # the sandbox, so a run of Enters never lands on production.
    for line in messages.describe_environment_choices():
        console.write(line)
    default = str(ENVIRONMENT_ORDER.index(config.DEFAULT_ENVIRONMENT) + 1)
    while True:
        answer = ask_with_default(
            console,
            prompt="Wybierz środowisko (numer albo nazwa)",
            default=default,
        ).lower()
        chosen = resolve_environment(answer)
        if chosen is not None:
            return chosen
        console.write(f"Podaj numer od 1 do {len(ENVIRONMENT_ORDER)} albo nazwę.")


def choose_working_directory(console: Console, *, nip: str) -> Path:
    """Name the directory by what it receives: statements and rendered PDFs.

    It never receives the invoice XML — that goes to the archive, whose place is
    fixed (D-032). Asking for a „katalog na pobrane faktury" promised otherwise,
    and the taxpayer judged backup and privacy by the wrong path (GH-188).
    """
    answer = ask_with_default(
        console,
        prompt="Katalog roboczy na zestawienia i PDF-y",
        default=str(Path.home() / "ksef" / nip),
    )
    directory = Path(answer).expanduser()
    prepared = config.prepare_directory(directory)
    for line in messages.describe_working_directory_choice(
        prepared,
        cloud_marker=config.cloud_sync_marker(directory),
    ):
        console.write(line)
    return prepared.path


def report_archive_location(
    console: Console,
    *,
    nip: str,
    environment: KsefEnvironment,
) -> None:
    directory = InvoiceArchive(nip=nip, environment=environment).invoice_directory
    for line in messages.describe_archive_location(
        directory,
        cloud_marker=config.cloud_sync_marker(directory),
    ):
        console.write(line)


def summarize_configuration(
    console: Console,
    configuration: Configuration,
    *,
    saved_to: Path,
) -> None:
    for line in messages.describe_configuration(configuration, saved_to=saved_to):
        console.write(line)


def registration_command() -> str:
    return f"claude mcp add {SERVER_NAME} -- uvx {SERVER_NAME}"


def offer_client_registration(console: Console) -> None:
    """Register the server with the MCP client, so the taxpayer can just ask.

    Default yes: this writes a line to a client config and reaches nothing —
    no KSeF call, no budget. Idempotent, because re-running onboarding while
    fixing a setting must not leave two entries behind.
    """
    console.write("")
    if not affirmative(
        ask_with_default(console, prompt="Podłączyć serwer do klienta MCP? (t/n)", default="t")
    ):
        console.write("  Pomijam. Później: " + registration_command())
        return
    if not client.command_available():
        for line in messages.describe_manual_registration(registration_command()):
            console.write(line)
        return
    if client.already_registered(SERVER_NAME):
        console.write(f"  Serwer {SERVER_NAME} jest już zarejestrowany — nic nie zmieniam.")
        return
    client.register(SERVER_NAME)
    console.write(f"  Zarejestrowany jako {SERVER_NAME}.")


def offer_skill_install(console: Console, *, home: Path, working_directory: Path) -> None:
    """The skill is what tells the agent these tools exist and how to use them."""
    console.write("")
    if not affirmative(
        ask_with_default(console, prompt="Zainstalować skill dla agenta? (t/n)", default="t")
    ):
        console.write("  Pomijam. Później: ksef-mcp skill install --scope user")
        return
    run_skill_install(
        console,
        scope=SkillScope.USER,
        home=home,
        working_directory=working_directory,
    )


def offer_connection_check(console: Console, *, configuration_file: Path | None) -> int:
    """Default NO, and that is the whole point.

    Onboarding gets re-run while a setting is being corrected. If a run of
    Enters reached KSeF each time, a person fixing a typo in their NIP would
    spend the hourly allowance the Ministry polices — and repeated breaches
    lengthen a block (D-020, D-031 §8).
    """
    console.write("")
    if not affirmative(
        ask_with_default(console, prompt="Sprawdzić teraz połączenie z KSeF? (t/N)", default="n")
    ):
        console.write("Połączenia nie sprawdzam — każde zapytanie zjada")
        console.write("godzinowy budżet, także przy przebiegu poprawkowym.")
        console.write("Gdy zechcesz to potwierdzić: ksef-mcp verify")
        return EXIT_OK
    console.write("")
    return run_verify(console, configuration_file=configuration_file)


def run_onboarding(
    console: Console,
    *,
    working_directory: Path,
    configuration_file: Path | None,
    home: Path,
) -> int:
    console.write(f"{SERVER_NAME} {VERSION} — konfiguracja przed pierwszym uruchomieniem")
    console.write("")
    keyring_report = report_preflight(console, working_directory=working_directory)
    if not keyring_report.usable:
        console.write("")
        console.write("Bez magazynu nie zapiszę tokenu i nie zapytam o hasło —")
        console.write("interaktywny prompt zawiesiłby transport MCP. Przerywam.")
        return EXIT_UNUSABLE_KEYRING
    console.write("")
    nip = str(ask_nip(console))
    backend = choose_keyring_backend(console, keyring_report)
    environment = choose_environment(console)
    explain_token_step(console)
    token = ask_secret_required(console, prompt="Wklej token KSeF (bez echa)")
    stored = token_store.store_token(nip=nip, token=token)
    console.write(
        f"  Token zapisany i odczytany z powrotem: {stored.length} znaków, "
        f"końcówka …{stored.suffix}"
    )
    directory = choose_working_directory(console, nip=nip)
    report_archive_location(console, nip=nip, environment=environment)
    configuration = Configuration(
        nip=nip,
        environment=environment,
        keyring_backend=backend,
        working_directory=directory,
    )
    saved_to = config.save_configuration(configuration, path=configuration_file)
    summarize_configuration(console, configuration, saved_to=saved_to)
    offer_client_registration(console)
    offer_skill_install(console, home=home, working_directory=working_directory)
    return offer_connection_check(console, configuration_file=configuration_file)
