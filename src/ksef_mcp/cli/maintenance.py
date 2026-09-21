"""Freeing the disk without forgetting that the invoices were ever held (D-034)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ksef_mcp import config, messages
from ksef_mcp.cli.console import Console, affirmative, ask_with_default
from ksef_mcp.cli.exits import (
    EXIT_INVALID_NIP,
    EXIT_INVALID_WINDOW,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
    EXIT_PURGE_DECLINED,
)
from ksef_mcp.paths import Nip, NipRejected
from ksef_mcp.retention import ArchivePurge, PurgeWindow, PurgeWindowInverted, purge_entry
from ksef_mcp.storage.archive import InvoiceArchive
from ksef_mcp.storage.audit import AuditTrail, Authorisation, AuthorisationBasis


def confirm_purge(console: Console, *, count: int) -> bool:
    return affirmative(
        ask_with_default(
            console,
            prompt=f"Skasować {count} faktur bezpowrotnie? (t/n)",
            default="n",
        )
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
