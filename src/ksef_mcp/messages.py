"""What the command line says, with nothing that decides what to say.

Split out of `cli.py` because the two kinds of function were interleaved
there: one asks the person something or picks a branch, the other turns a
value into lines. Only the second kind lives here, so every function below
takes data and returns text — no `Console`, no input, no exit codes. That
is what makes them testable by equality rather than by capturing output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ksef_mcp import config, preflight, token_store
from ksef_mcp.config import Configuration
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.retention import PurgePlan, PurgeWindow

if TYPE_CHECKING:
    from pathlib import Path

    from ksef_mcp import ksef_port


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


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


def describe_collection_lock(state: preflight.CollectionLock) -> tuple[str, ...]:
    if state is preflight.CollectionLock.LOCKED:
        return (
            "  Kolekcja: zablokowana — odblokuj ją w sesji graficznej.",
            "  O hasło nie pytam: prompt zawiesiłby transport MCP, więc do",
            f"  tokenu nie sięgam wcale. Awaryjnie: {token_store.FALLBACK_ENVIRONMENT_VARIABLE}.",
        )
    if state is preflight.CollectionLock.UNLOCKED:
        return ("  Kolekcja: odblokowana.",)
    # ABSENT says nothing about health — macOS and Windows have no Secret
    # Service at all — and a line about it would read like a fault.
    return ()


def describe_environment_choices() -> tuple[str, ...]:
    """The difference between test and demo is not obvious from the names."""
    return (
        "Środowisko KSeF:",
        "  1. test — piaskownica, limity dziesięciokrotnie wyższe niż produkcja",
        "  2. demo — odpowiednik produkcji, te same limity, dane nieoficjalne",
        "  3. production — prawdziwe faktury i prawdziwe limity",
    )


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


def describe_configuration(configuration: Configuration, *, saved_to: Path) -> tuple[str, ...]:
    return (
        "",
        "Konfiguracja zapisana:",
        f"  NIP: {configuration.nip}",
        f"  Środowisko: {configuration.environment}",
        f"  Magazyn tokenu: {configuration.keyring_backend}",
        f"  Katalog faktur: {configuration.invoice_directory}",
        f"  Plik: {saved_to}",
    )


def describe_manual_registration(command: str) -> tuple[str, ...]:
    """When `claude` is not on PATH, the person still deserves the exact text."""
    return (
        "  Nie znalazłem polecenia `claude` w PATH — rejestruję ręcznie.",
        "  Skopiuj to:",
        f"    {command}",
        "  Albo dopisz do konfiguracji klienta (Claude Desktop, Cursor):",
        '    "mcpServers": {',
        f'      "{SERVER_NAME}": {{',
        '        "command": "uvx",',
        f'        "args": ["{SERVER_NAME}"]',
        "      }",
        "    }",
    )


def describe_invoices(invoices: tuple[ksef_port.InvoiceMetadata, ...]) -> tuple[str, ...]:
    return tuple(
        f"  {invoice.issue_date}  {invoice.gross_amount:>12,.2f} {invoice.currency}  "
        f"{invoice.seller_name or invoice.seller_nip}"
        for invoice in invoices
    )


def describe_failure(configuration: Configuration, failure: Exception) -> str:
    # Which subject and in which environment, on the same line as the reason:
    # with two NIP-y configured, "KSeF odrzucił token" alone leaves the person
    # guessing whose token was rejected and against which registry.
    return (
        f"Nie potwierdziłem połączenia dla {configuration.nip} "
        f"({configuration.environment}): {failure}"
    )


def describe_retry_after(retry_after: int | None) -> str:
    if retry_after is None:
        # No Retry-After means no ceiling we can quote; guessing a wait and
        # retrying is the behaviour the Ministry penalises.
        return "Odczekaj przed kolejną próbą — nie ponawiam samoczynnie."
    return f"Odczekaj {retry_after} s przed kolejną próbą — nie ponawiam samoczynnie."


def describe_source(source: token_store.TokenSource) -> str:
    if source is token_store.TokenSource.ENVIRONMENT:
        return f"zmiennej {token_store.FALLBACK_ENVIRONMENT_VARIABLE}"
    return "keyringu systemowego"


def describe_window(window: PurgeWindow) -> str:
    begins = (
        "od początku archiwum"
        if window.received_from is None
        else f"od {window.received_from.isoformat()}"
    )
    ends = "do dziś" if window.received_to is None else f"do {window.received_to.isoformat()}"
    return f"Zakres: faktury z datą wpływu do KSeF {begins} {ends}."


def describe_size(size_bytes: int) -> str:
    return f"{size_bytes / 1024:.1f} kB"


def describe_purge_plan(plan: PurgePlan) -> tuple[str, ...]:
    listed = tuple(
        f"  {candidate.ksef_number}  {candidate.received_on.isoformat()}"
        for candidate in plan.candidates
    )
    # Named one by one, not counted: this is the last moment before the bodies
    # stop existing, and a number the operator did not expect to see here is the
    # only warning that the filter is wider than they meant it to be.
    return (
        f"Katalog: {plan.directory}",
        describe_window(plan.window),
        "",
        f"Do skasowania ({len(plan.candidates)}, {describe_size(plan.freed_bytes)}):",
        *listed,
        f"Zostaje w archiwum: {len(plan.retained)}.",
        *describe_unrecognised(plan),
    )


def describe_unrecognised(plan: PurgePlan) -> tuple[str, ...]:
    if not plan.unrecognised:
        return ()
    # Left alone deliberately: an irreversible operation touches only the files
    # whose own name says they are invoices this archive wrote.
    return (
        f"Zostawiam {len(plan.unrecognised)} plików, których nazwa nie jest "
        f"numerem KSeF — pierwszy z nich: {plan.unrecognised[0]}",
    )
