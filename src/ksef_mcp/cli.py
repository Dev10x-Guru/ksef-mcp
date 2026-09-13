from __future__ import annotations

import argparse
import getpass
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ksef_mcp import config, preflight, skill, token_store
from ksef_mcp.config import Configuration, KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.server import main as run_mcp_server
from ksef_mcp.skill import SkillScope

if TYPE_CHECKING:
    from ksef_mcp import ksef_port

TOKEN_PORTAL_URL: Final[str] = "https://ap.ksef.mf.gov.pl/web/tokens/generate-token"

EXIT_OK: Final[int] = 0

EXIT_UNUSABLE_KEYRING: Final[int] = 1

EXIT_NO_TOKEN: Final[int] = 2

EXIT_NOT_CONFIGURED: Final[int] = 3

EXIT_KSEF_REFUSED: Final[int] = 4

EXIT_SKILL_KEPT: Final[int] = 5

AFFIRMATIVE_ANSWERS: Final[frozenset[str]] = frozenset({"t", "tak"})


@dataclass(frozen=True)
class Console:
    write: Callable[[str], None]
    ask: Callable[[str], str]
    ask_secret: Callable[[str], str]


def default_console() -> Console:
    return Console(write=print, ask=input, ask_secret=getpass.getpass)


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


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


def describe_requirement_source(report: preflight.NodeReport) -> str:
    if report.pinned_by is None:
        return "minimum generatora MF"
    # Naming both numbers: otherwise the reader sees a requirement that appears
    # in none of their own files and cannot tell where it came from.
    if report.pin_is_below_the_generator:
        return (
            f"pin {format_version(report.pinned)} w {report.pinned_by} jest niższy "
            "niż minimum generatora MF, biorę wyższe"
        )
    return f"z {report.pinned_by}"


def describe_node(report: preflight.NodeReport) -> tuple[str, ...]:
    required = format_version(report.required)
    source = describe_requirement_source(report)
    if report.version is None:
        return (
            f"  Node: nie znaleziono, a wymagane jest {required} ({source}).",
            f"  Instalacja: fnm install {required}",
            "  Dopisz `fnm env` do profilu powłoki — bez tego .node-version",
            "  jest deklaracją, nie egzekucją, a wersja cicho się rozjeżdża.",
        )
    found = format_version(report.version)
    # The path matters with fnm: shims make "which node exactly" the whole
    # question when the version turns out to be the wrong one.
    where = f"  Ścieżka: {report.executable}"
    if report.satisfies_requirement:
        return (f"  Node: {found} — spełnia wymaganie {required} ({source}).", where)
    return (
        f"  Node: {found} jest starsze niż wymagane {required} ({source}).",
        where,
        f"  Podnieś wersję: fnm install {required}",
    )


def describe_keyring(report: preflight.KeyringReport) -> tuple[str, ...]:
    if not report.usable:
        return (
            "  Keyring: brak dostępnego magazynu (headless, WSL, kontener).",
            f"  Ścieżka awaryjna: wyeksportuj zmienną {token_store.FALLBACK_ENVIRONMENT_VARIABLE}.",
        )
    lines = ["  Keyring: dostępne magazyny —"]
    for index, backend in enumerate(report.backends, start=1):
        marker = " — domyślny" if backend.module == report.preferred else ""
        lines.append(f"    {index}. {backend.module} (priorytet {backend.priority:g}){marker}")
    return tuple(lines)


def preferred_backend_index(report: preflight.KeyringReport) -> int:
    return next(
        index
        for index, backend in enumerate(report.backends, start=1)
        if backend.module == report.preferred
    )


def choose_keyring_backend(console: Console, report: preflight.KeyringReport) -> str:
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


def choose_environment(console: Console) -> KsefEnvironment:
    while True:
        answer = ask_with_default(
            console,
            prompt="Środowisko KSeF (test/demo/production)",
            default=str(config.DEFAULT_ENVIRONMENT),
        ).lower()
        try:
            return KsefEnvironment(answer)
        except ValueError:
            console.write("Dozwolone wartości: test, demo, production.")


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
    for line in describe_invoice_directory(prepared):
        console.write(line)
    return prepared.path


def describe_invoice_directory(prepared: config.InvoiceDirectory) -> tuple[str, ...]:
    if prepared.created:
        return (f"  Katalog utworzony z uprawnieniami 0700: {prepared.path}",)
    # Permissions on a directory that already existed are left alone — someone
    # may have pointed this at a home or a shared path, and tightening it
    # would be a change they did not ask for.
    return (
        f"  Katalog już istniał, zostawiam uprawnienia {prepared.mode:04o}: {prepared.path}",
        "  Jeśli ma być prywatny: chmod 0700 " + str(prepared.path),
    )


def report_preflight(console: Console, *, working_directory: Path) -> preflight.KeyringReport:
    console.write("Warunki wstępne:")
    for line in describe_node(preflight.inspect_node(working_directory=working_directory)):
        console.write(line)
    keyring_report = preflight.inspect_keyring()
    for line in describe_keyring(keyring_report):
        console.write(line)
    return keyring_report


def summarize(console: Console, configuration: Configuration, *, saved_to: Path) -> None:
    console.write("")
    console.write("Konfiguracja zapisana:")
    console.write(f"  NIP: {configuration.nip}")
    console.write(f"  Środowisko: {configuration.environment}")
    console.write(f"  Magazyn tokenu: {configuration.keyring_backend}")
    console.write(f"  Katalog faktur: {configuration.invoice_directory}")
    console.write(f"  Plik: {saved_to}")
    console.write("")
    # Deliberately not run here: onboarding is re-run while settings are being
    # fixed, and every KSeF call spends an hourly budget the Ministry polices.
    # The check is a command the person invokes when they mean to.
    console.write("Połączenia stąd nie sprawdzam — każde zapytanie zjada")
    console.write("godzinowy budżet, także przy przebiegu poprawkowym.")
    console.write("Gdy zechcesz to potwierdzić: ksef-mcp verify")


def run_onboarding(
    console: Console,
    *,
    working_directory: Path,
    configuration_file: Path | None,
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
    nip = ask_required(console, prompt="NIP podmiotu")
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
    summarize(console, configuration, saved_to=saved_to)
    return EXIT_OK


def describe_invoices(invoices: tuple[ksef_port.InvoiceMetadata, ...]) -> tuple[str, ...]:
    return tuple(
        f"  {invoice.issue_date}  {invoice.gross_amount:>12,.2f} {invoice.currency}  "
        f"{invoice.seller_name or invoice.seller_nip}"
        for invoice in invoices
    )


def run_verify(console: Console, *, configuration_file: Path | None) -> int:
    # Imported here, not at module scope: ksef2 pulls lxml, signxml and xsdata,
    # which costs about half a second. Only this command talks to KSeF, and
    # `token status` can run in a loop from a script.
    from ksef_mcp import ksef_port
    from ksef_mcp.ksef_port.adapter import Ksef2Port

    configuration = config.load_configuration(path=configuration_file)
    if configuration is None:
        console.write("Brak konfiguracji. Uruchom najpierw: ksef-mcp onboarding")
        return EXIT_NOT_CONFIGURED
    stored = token_store.read_token(nip=configuration.nip)
    if stored is None:
        console.write(
            f"Brak tokenu dla {configuration.nip}. "
            f"Zapisz go: ksef-mcp token set --nip {configuration.nip}"
        )
        return EXIT_NO_TOKEN
    console.write(f"Odpytuję KSeF ({configuration.environment}) jako {configuration.nip}…")
    console.write(f"Token pochodzi z: {describe_source(stored.source)}")
    if stored.source is token_store.TokenSource.ENVIRONMENT:
        console.write(
            f"  Zmienna nie jest przypisana do NIP-u, więc nie gwarantuję, że "
            f"należy do {configuration.nip}."
        )
    try:
        checked = ksef_port.check_connection(
            port=Ksef2Port(environment=configuration.environment),
            nip=configuration.nip,
            token=stored.value,
        )
    except ksef_port.KsefRateLimited as refusal:
        console.write(str(refusal))
        console.write(describe_retry_after(refusal.retry_after))
        return EXIT_KSEF_REFUSED
    except ksef_port.KsefPortError as failure:
        console.write(str(failure))
        return EXIT_KSEF_REFUSED
    return report_connection(console, checked)


def describe_retry_after(retry_after: int | None) -> str:
    if retry_after is None:
        # No Retry-After means no ceiling we can quote; guessing a wait and
        # retrying is the behaviour the Ministry penalises.
        return "Odczekaj przed kolejną próbą — nie ponawiam samoczynnie."
    return f"Odczekaj {retry_after} s przed kolejną próbą — nie ponawiam samoczynnie."


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
    for line in describe_invoices(checked.invoices):
        console.write(line)
    return EXIT_OK


def run_doctor(console: Console, *, working_directory: Path) -> int:
    console.write(f"{SERVER_NAME} {VERSION}")
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


def describe_source(source: token_store.TokenSource) -> str:
    if source is token_store.TokenSource.ENVIRONMENT:
        return f"zmiennej {token_store.FALLBACK_ENVIRONMENT_VARIABLE}"
    return "keyringu systemowego"


def run_token_status(console: Console, *, nip: str) -> int:
    stored = token_store.read_token(nip=nip)
    if stored is None:
        console.write(f"Brak tokenu dla {nip}. Zapisz go: ksef-mcp token set --nip {nip}")
        return EXIT_NO_TOKEN
    described = token_store.fingerprint(stored.value)
    console.write(
        f"Token dla {nip} pochodzi z {describe_source(stored.source)}: "
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
    )
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("onboarding", help="Przeprowadza przez konfigurację")
    subcommands.add_parser("doctor", help="Sprawdza warunki wstępne i kończy")
    subcommands.add_parser("verify", help="Odpytuje KSeF i pokazuje ostatnie faktury")

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
        )
    if arguments.command == "doctor":
        return run_doctor(console, working_directory=working_directory)
    if arguments.command == "verify":
        return run_verify(console, configuration_file=configuration_file)
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
        return handlers[arguments.token_command](console, nip=arguments.nip)
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
    # This message was written for exactly this moment; a traceback would bury
    # the one instruction that gets the person unstuck.
    except token_store.TokenStoreUnavailable as unavailable:
        reporting.write(str(unavailable))
        return EXIT_UNUSABLE_KEYRING
