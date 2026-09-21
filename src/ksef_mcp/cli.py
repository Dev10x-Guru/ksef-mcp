from __future__ import annotations

import argparse
import getpass
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Final

from ksef_mcp import (
    config,
    keyring_preflight,
    ksef_port,
    messages,
)
from ksef_mcp.allowance import Allowance
from ksef_mcp.config import Configuration
from ksef_mcp.diagnostics import technical_log
from ksef_mcp.ksef_port.lazy import load_adapter
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.paths import Nip, NipRejected, SubjectScope
from ksef_mcp.rendering import node_preflight
from ksef_mcp.retention import (
    ArchivePurge,
    PurgeWindow,
    PurgeWindowInverted,
    purge_entry,
)
from ksef_mcp.server import main as run_mcp_server
from ksef_mcp.setup import client, skill
from ksef_mcp.setup.skill import SkillScope
from ksef_mcp.storage import token_store
from ksef_mcp.storage.archive import InvoiceArchive
from ksef_mcp.storage.audit import AuditTrail, Authorisation, AuthorisationBasis
from ksef_mcp.storage.period_cache import MeteredPeriods, PeriodCache

TOKEN_PORTAL_URL: Final[str] = "https://ap.ksef.mf.gov.pl/web/tokens/generate-token"

EXIT_OK: Final[int] = 0

EXIT_UNUSABLE_KEYRING: Final[int] = 1

EXIT_NO_TOKEN: Final[int] = 2

EXIT_NOT_CONFIGURED: Final[int] = 3

EXIT_KSEF_REFUSED: Final[int] = 4

EXIT_SKILL_KEPT: Final[int] = 5

EXIT_PURGE_DECLINED: Final[int] = 6

EXIT_INVALID_WINDOW: Final[int] = 7

EXIT_INVALID_NIP: Final[int] = 8

EXIT_UNREADABLE_CONFIGURATION: Final[int] = 9

AFFIRMATIVE_ANSWERS: Final[frozenset[str]] = frozenset({"t", "tak"})


@dataclass(frozen=True)
class Console:
    write: Callable[[str], None]
    ask: Callable[[str], str]
    ask_secret: Callable[[str], str]


def default_console() -> Console:
    return Console(write=print, ask=input, ask_secret=getpass.getpass)


def ask_with_default(console: Console, *, prompt: str, default: str) -> str:
    return console.ask(f"{prompt} [{default}]: ").strip() or default


def ask_required(console: Console, *, prompt: str) -> str:
    while True:
        answer = console.ask(f"{prompt}: ").strip()
        if answer:
            return answer
        console.write("Wartość jest wymagana.")


def ask_secret_required(console: Console, *, prompt: str) -> str:
    while True:
        answer = console.ask_secret(f"{prompt}: ").strip()
        if answer:
            return answer
        console.write("Token jest wymagany.")


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


ENVIRONMENT_ORDER: Final[tuple[KsefEnvironment, ...]] = (
    KsefEnvironment.TEST,
    KsefEnvironment.DEMO,
    KsefEnvironment.PRODUCTION,
)


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


def choose_invoice_directory(console: Console, *, nip: str) -> Path:
    answer = ask_with_default(
        console,
        prompt="Katalog na pobrane faktury",
        default=str(Path.home() / "ksef" / nip),
    )
    directory = Path(answer).expanduser()
    marker = config.cloud_sync_marker(directory)
    if marker is not None:
        console.write(f"  Uwaga: ścieżka wygląda na synchronizowaną ({marker}).")
        console.write("  Pobrane faktury zawierają dane osobowe kontrahentów.")
    prepared = config.prepare_invoice_directory(directory)
    for line in messages.describe_invoice_directory(prepared):
        console.write(line)
    return prepared.path


def report_preflight(
    console: Console,
    *,
    working_directory: Path,
) -> keyring_preflight.KeyringReport:
    console.write("Warunki wstępne:")
    node = node_preflight.inspect_node(working_directory=working_directory)
    for line in messages.describe_node(node):
        console.write(line)
    keyring_report = keyring_preflight.inspect_keyring()
    for line in messages.describe_keyring(keyring_report):
        console.write(line)
    for line in messages.describe_collection_lock(keyring_preflight.inspect_collection_lock()):
        console.write(line)
    return keyring_report


def summarize_configuration(
    console: Console,
    configuration: Configuration,
    *,
    saved_to: Path,
) -> None:
    for line in messages.describe_configuration(configuration, saved_to=saved_to):
        console.write(line)


def affirmative(answer: str) -> bool:
    return answer.lower() in AFFIRMATIVE_ANSWERS


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
    directory = choose_invoice_directory(console, nip=nip)
    configuration = Configuration(
        nip=nip,
        environment=environment,
        keyring_backend=backend,
        invoice_directory=directory,
    )
    saved_to = config.save_configuration(configuration, path=configuration_file)
    summarize_configuration(console, configuration, saved_to=saved_to)
    offer_client_registration(console)
    offer_skill_install(console, home=home, working_directory=working_directory)
    return offer_connection_check(console, configuration_file=configuration_file)


def metered_periods(configuration: config.Configuration) -> MeteredPeriods:
    """`verify` answering from the same disk, and paying from the same counter.

    The subject and the environment come from the one configuration the command
    already loaded, so the files this touches are the ones the MCP tools touch.
    A second spelling of either would give `verify` a private cache and a
    private allowance, which is the bypass it is being taken out of (GH-98).
    """
    return MeteredPeriods(
        cache=PeriodCache(nip=configuration.nip, environment=configuration.environment),
        allowance=Allowance(nip=configuration.nip, environment=configuration.environment),
    )


def run_verify(console: Console, *, configuration_file: Path | None) -> int:
    loaded = config.load_configuration(path=configuration_file)
    if loaded is None:
        console.write(messages.describe_not_configured())
        return EXIT_NOT_CONFIGURED
    # The same spelling the MCP tools resolve to, so `verify` reads the cache
    # and spends the allowance of the subject those tools work as (GH-98).
    try:
        configuration = replace(loaded, nip=str(Nip.parsed(loaded.nip)))
    except NipRejected:
        console.write(messages.describe_rejected_nip())
        return EXIT_INVALID_NIP
    stored = token_store.read_token(nip=configuration.nip)
    if stored is None:
        console.write(
            f"Brak tokenu dla {configuration.nip}. "
            f"Zapisz go: ksef-mcp token set --nip {configuration.nip}"
        )
        return EXIT_NO_TOKEN
    console.write(f"Odpytuję KSeF ({configuration.environment}) jako {configuration.nip}…")
    console.write(f"Token pochodzi z: {messages.describe_source(stored.source)}")
    if stored.source is token_store.TokenSource.ENVIRONMENT:
        console.write(
            f"  Zmienna nie jest przypisana do NIP-u, więc nie gwarantuję, że "
            f"należy do {configuration.nip}."
        )
    port = load_adapter()(environment=configuration.environment)
    try:
        checked = ksef_port.check_connection(
            port=port,
            nip=configuration.nip,
            token=stored,
            readers=metered_periods(configuration),
        )
    except ksef_port.KsefRateLimited as refusal:
        console.write(messages.describe_failure(configuration, refusal))
        console.write(messages.describe_retry_after(refusal.retry_after))
        return EXIT_KSEF_REFUSED
    except ksef_port.KsefPortError as failure:
        console.write(messages.describe_failure(configuration, failure))
        return EXIT_KSEF_REFUSED
    return report_connection(console, checked)


def report_connection(console: Console, checked: ksef_port.ConnectionCheck) -> int:
    if checked.subject_name is not None:
        console.write(f"Działa — jesteś połączony jako {checked.subject_name}.")
    else:
        console.write("Połączenie i token działają.")
    console.write(f"Środowisko: {checked.environment}")
    if not checked.invoices:
        console.write("W ostatnich 30 dniach nie ma faktur zakupowych.")
        console.write("Połączenie jest potwierdzone — pusty wynik to nie błąd.")
        return EXIT_OK
    console.write("")
    console.write(f"Ostatnie faktury zakupowe ({len(checked.invoices)}):")
    for line in messages.describe_invoices(checked.invoices):
        console.write(line)
    return EXIT_OK


def stranded_directories(configuration: Configuration | None) -> tuple[Path, ...]:
    """Files this version would no longer look at, for `doctor` to point out.

    A configuration whose NIP cannot be read at all is not this function's
    problem — `verify` and every tool say so in their own words — so it answers
    "nothing stranded" rather than raising inside a diagnostic command.
    """
    if configuration is None:
        return ()
    try:
        scope = SubjectScope.parsed(nip=configuration.nip, environment=configuration.environment)
    except NipRejected:
        return ()
    return scope.unnormalised_twins()


def run_doctor(
    console: Console, *, working_directory: Path, configuration_file: Path | None
) -> int:
    console.write(f"{SERVER_NAME} {VERSION}")
    console.write("")
    console.write("Tożsamość:")
    for line in messages.describe_identity(shutil.which(SERVER_NAME)):
        console.write(line)
    configuration = config.load_configuration(path=configuration_file)
    for line in messages.describe_subject(configuration):
        console.write(line)
    for line in messages.describe_unnormalised_twins(stranded_directories(configuration)):
        console.write(line)
    console.write("")
    report_preflight(console, working_directory=working_directory)
    return EXIT_OK


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


def confirm_overwrite(console: Console) -> bool:
    answer = ask_with_default(
        console,
        prompt="Nadpisać zainstalowany skill? (t/n)",
        default="n",
    )
    return answer.lower() in AFFIRMATIVE_ANSWERS


def run_skill_install(
    console: Console,
    *,
    scope: SkillScope,
    home: Path,
    working_directory: Path,
) -> int:
    comparison = skill.compare_skill(
        skill.skill_path(scope, home=home, working_directory=working_directory)
    )
    console.write(f"Zakres: {scope} — {comparison.path}")
    if comparison.absent:
        skill.write_skill(comparison)
        console.write(f"Skill zainstalowany dla wersji {VERSION}.")
        return EXIT_OK
    if comparison.up_to_date:
        console.write(f"Skill jest aktualny dla wersji {VERSION} — nic nie zmieniam.")
        return EXIT_OK
    console.write("Zainstalowany skill różni się od nowego:")
    for line in skill.describe_difference(comparison):
        console.write(f"  {line}")
    if not confirm_overwrite(console):
        console.write("Zostawiam zainstalowany skill bez zmian.")
        return EXIT_SKILL_KEPT
    skill.write_skill(comparison)
    console.write(f"Skill zaktualizowany do wersji {VERSION}.")
    return EXIT_OK


def confirm_purge(console: Console, *, count: int) -> bool:
    return (
        ask_with_default(
            console,
            prompt=f"Skasować {count} faktur bezpowrotnie? (t/n)",
            default="n",
        ).lower()
        in AFFIRMATIVE_ANSWERS
    )


def run_purge(
    console: Console,
    *,
    nip: str | None,
    received_from: date | None,
    received_to: date | None,
    configuration_file: Path | None,
) -> int:
    configuration = config.load_configuration(path=configuration_file)
    if configuration is None:
        console.write(messages.describe_not_configured())
        return EXIT_NOT_CONFIGURED
    # Before the window and before anything is planned: this value becomes a
    # path segment and this command deletes files, so `--nip ../../..` has to
    # die at the boundary rather than be caught by the confirmation prompt.
    # Confirming is a guard against a mistake, never against bad input (GH-112).
    try:
        subject = str(Nip.parsed(configuration.nip if nip is None else nip))
    except NipRejected:
        console.write(messages.describe_refused_nip())
        return EXIT_INVALID_NIP
    try:
        window = PurgeWindow(received_from=received_from, received_to=received_to)
    except PurgeWindowInverted as inverted:
        console.write(str(inverted))
        return EXIT_INVALID_WINDOW
    archive = InvoiceArchive(nip=subject, environment=configuration.environment)
    purge = ArchivePurge(archive=archive)
    plan = purge.plan(window=window)
    console.write(f"Archiwum podmiotu {subject} ({configuration.environment})")
    for line in messages.describe_purge_plan(plan):
        console.write(line)
    if not plan.candidates:
        console.write("Nic nie pasuje do tego zakresu — nie kasuję niczego.")
        return EXIT_OK
    console.write("")
    console.write("Nie ruszam indeksu deduplikacji, punktów kontynuacji ani")
    console.write("dziennika przeglądu — po wyczyszczeniu synchronizacja nie")
    console.write("ściągnie tych faktur powtórnie.")
    if not confirm_purge(console, count=len(plan.candidates)):
        console.write("Zostawiam archiwum bez zmian.")
        return EXIT_PURGE_DECLINED
    report = purge.remove(plan=plan)
    trail = AuditTrail(nip=subject, environment=configuration.environment)
    recorded = trail.record(
        (
            purge_entry(
                report,
                authorisation=Authorisation(
                    nip=subject,
                    environment=configuration.environment,
                    basis=AuthorisationBasis.OPERATOR,
                ),
                moment=trail.clock(),
            ),
        )
    )
    console.write(
        f"Skasowane faktury: {len(report.purged)}, "
        f"zwolnione {messages.describe_size(report.freed_bytes)}."
    )
    console.write(f"Indeks deduplikacji pamięta nadal {report.still_known} numerów KSeF:")
    console.write(f"  {report.index_path}")
    console.write(f"Wpis w dzienniku audytu: {recorded}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description=(
            "Serwer MCP dla KSeF. Bez argumentów uruchamia serwer na stdio, "
            "czego oczekuje klient MCP."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {VERSION}")
    parser.set_defaults(
        command=None,
        nip=None,
        token_command=None,
        skill_command=None,
        scope=None,
        received_from=None,
        received_to=None,
    )
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("onboarding", help="Przeprowadza przez konfigurację")
    subcommands.add_parser("doctor", help="Sprawdza warunki wstępne i kończy")
    subcommands.add_parser("verify", help="Odpytuje KSeF i pokazuje ostatnie faktury")

    purge = subcommands.add_parser(
        "purge",
        help="Kasuje faktury z archiwum, zachowując indeks deduplikacji",
    )
    # The configured subject by default, but the subject dimension is offered:
    # an accounting office losing a client purges that client's own directory and
    # never a neighbour's (D-032, D-034).
    purge.add_argument("--nip", help="Podmiot do wyczyszczenia (domyślnie ten z konfiguracji)")
    purge.add_argument(
        "--od",
        dest="received_from",
        type=date.fromisoformat,
        help="Najwcześniejsza data wpływu do KSeF (RRRR-MM-DD), włącznie",
    )
    purge.add_argument(
        "--do",
        dest="received_to",
        type=date.fromisoformat,
        help="Najpóźniejsza data wpływu do KSeF (RRRR-MM-DD), włącznie",
    )

    token = subcommands.add_parser("token", help="Zarządza tokenem w keyringu")
    token_actions = token.add_subparsers(dest="token_command", required=True)
    for action, help_text in (
        ("set", "Zapisuje token odczytany ze stdin bez echa"),
        ("delete", "Usuwa token z magazynu"),
        ("status", "Mówi, czy token jest, nie pokazując wartości"),
    ):
        action_parser = token_actions.add_parser(action, help=help_text)
        action_parser.add_argument("--nip", required=True)

    skill_command = subcommands.add_parser("skill", help="Instaluje skill dla agenta")
    skill_actions = skill_command.add_subparsers(dest="skill_command", required=True)
    install = skill_actions.add_parser("install", help="Tworzy albo aktualizuje skill")
    # No default scope on purpose: uvx runs from whatever directory happens to
    # be current, so a silently assumed scope would write the skill somewhere
    # the person never meant to look for it.
    install.add_argument(
        "--scope",
        required=True,
        type=SkillScope,
        choices=tuple(SkillScope),
        help="user: ~/.claude/skills, project: ./.claude/skills",
    )
    return parser


def dispatch(
    arguments: argparse.Namespace,
    *,
    console: Console,
    working_directory: Path,
    configuration_file: Path | None,
    home: Path,
) -> int:
    if arguments.command == "onboarding":
        return run_onboarding(
            console,
            working_directory=working_directory,
            configuration_file=configuration_file,
            home=home,
        )
    if arguments.command == "doctor":
        return run_doctor(
            console,
            working_directory=working_directory,
            configuration_file=configuration_file,
        )
    if arguments.command == "verify":
        return run_verify(console, configuration_file=configuration_file)
    if arguments.command == "purge":
        return run_purge(
            console,
            nip=arguments.nip,
            received_from=arguments.received_from,
            received_to=arguments.received_to,
            configuration_file=configuration_file,
        )
    if arguments.command == "skill":
        return run_skill_install(
            console,
            scope=arguments.scope,
            home=home,
            working_directory=working_directory,
        )
    if arguments.command == "token":
        handlers = {
            "set": run_token_set,
            "delete": run_token_delete,
            "status": run_token_status,
        }
        # The same normalisation the archive gets, for the same reason: this
        # value is the keyring key, so two spellings would store two tokens for
        # one taxpayer and the second one would look like a token that vanished.
        try:
            subject = str(Nip.parsed(arguments.nip))
        except NipRejected:
            console.write(messages.describe_rejected_nip())
            return EXIT_INVALID_NIP
        return handlers[arguments.token_command](console, nip=subject)
    run_mcp_server()
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    console: Console | None = None,
    working_directory: Path | None = None,
    configuration_file: Path | None = None,
    home: Path | None = None,
) -> int:
    reporting = default_console() if console is None else console
    try:
        return dispatch(
            build_parser().parse_args(argv),
            console=reporting,
            working_directory=Path.cwd() if working_directory is None else working_directory,
            configuration_file=configuration_file,
            home=Path.home() if home is None else home,
        )
    # These messages were written for exactly this moment; a traceback would
    # bury the one instruction that gets the person unstuck.
    except token_store.TokenStoreUnavailable as unavailable:
        reporting.write(str(unavailable))
        return EXIT_UNUSABLE_KEYRING
    # Every command that reads the configuration reaches this, rather than each
    # of the three call sites catching it for itself (GH-119). A damaged file
    # stops all of them, and the remedy is the same sentence for all of them.
    except config.ConfigurationUnreadable as damaged:
        technical_log().warning("Configuration unreadable: %s", damaged)
        reporting.write(str(damaged))
        return EXIT_UNREADABLE_CONFIGURATION
