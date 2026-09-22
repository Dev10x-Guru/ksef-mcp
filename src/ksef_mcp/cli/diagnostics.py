"""What can be answered without KSeF, and the one command that asks it."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from ksef_mcp import config, keyring_preflight, ksef_port, messages, paths
from ksef_mcp.allowance import Allowance
from ksef_mcp.cli.console import Console
from ksef_mcp.cli.exits import (
    EXIT_INVALID_NIP,
    EXIT_KSEF_REFUSED,
    EXIT_NO_TOKEN,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
)
from ksef_mcp.ksef_port.lazy import load_adapter
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.paths import Nip, NipRejected
from ksef_mcp.rendering import node_preflight
from ksef_mcp.storage import token_store
from ksef_mcp.storage.period_cache import MeteredPeriods, PeriodCache


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
    except (
        ksef_port.BudgetExhausted,
        ksef_port.RefusalBreakerEngaged,
        ksef_port.KsefPortError,
    ) as failure:
        # Both local refusals named on their own beside the port root, not
        # folded into it (GH-211, GH-234). `check_connection` runs behind the
        # same `GuardedSession` as every other caller, so a spent counter and a
        # blown fuse both reach here — and both used to arrive as a
        # `KsefPortError`. Dropping either would answer `verify` with a
        # traceback in place of the sentence that says when asking resumes
        # (GH-243).
        console.write(messages.describe_failure(configuration, failure))
        return EXIT_KSEF_REFUSED
    return report_connection(console, checked)


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
    for line in messages.describe_unnormalised_subjects(paths.unnormalised_subjects()):
        console.write(line)
    console.write("")
    report_preflight(console, working_directory=working_directory)
    return EXIT_OK
