"""One month of purchase invoices, written as a CSV an accountant can forward."""

from pathlib import Path

from ksef_mcp.invoices.statement import (
    AccountingPeriod,
    Statement,
    StatementComposer,
    statement_entries,
)
from ksef_mcp.server.app import Journal, reported, server
from ksef_mcp.server.context import authenticated_dependencies
from ksef_mcp.server.results import GrossTotal, StatementResult
from ksef_mcp.storage.audit import AuditedOperation


def describe_statement(statement: Statement) -> StatementResult:
    return StatementResult(
        nip=statement.nip,
        environment=str(statement.environment),
        period=str(statement.period),
        path=statement.path,
        row_count=statement.row_count,
        gross_totals=[
            GrossTotal(currency=total.currency, gross=total.gross)
            for total in statement.gross_totals
        ],
        complete=statement.complete,
        from_cache=statement.from_cache,
        queried_at=statement.queried_at.isoformat(),
        message=statement.message,
        warnings=list(statement.warnings),
    )


def export_statement(
    *,
    period: str,
    working_directory: str | None,
    journal: Journal,
) -> StatementResult:
    subject, stored = authenticated_dependencies()
    directory = (
        subject.configuration.working_directory
        if working_directory is None
        else Path(working_directory).expanduser()
    )
    composer = StatementComposer(
        port=subject.port,
        cache=subject.cache,
        archive=subject.archive,
        allowance=subject.allowance,
    )
    statement = composer.run(
        nip=subject.nip,
        token=stored,
        period=AccountingPeriod.parsed(period),
        directory=directory,
    )
    trail = subject.trail
    journal.record(
        trail=trail,
        entries=statement_entries(
            statement,
            authorisation=subject.authorisation(stored),
            moment=trail.clock(),
        ),
    )
    return describe_statement(statement)


@server.tool()
def export_period_statement(
    period: str,
    working_directory: str | None = None,
) -> StatementResult:
    """Write one month of purchase invoices as a CSV an accountant can forward.

    `period` is a calendar month spelled `YYYY-MM`. The month is the unit a
    period is closed in, and both ends being fixed is what lets the same request
    be answered from disk instead of spending one of twenty metadata queries an
    hour a second time.

    `working_directory` overrides the directory declared during onboarding for
    this one call. Whichever is used is created `0700` if it is new, is refused
    outright if it lies inside the cache or data root — those are internal
    storage and deleting statements must never reach the archive — and is
    reported in `warnings` when its path looks like a cloud sync folder.

    The file holds ten columns, in this order: KSeF number, the seller's own
    invoice number, issue date, seller NIP, seller name, gross, net, VAT,
    currency, and a KOD I verification code. The code is composed from the
    seller NIP, the issue date and the SHA-256 of the archived invoice body;
    rows whose body is not in the archive yet say so instead of carrying a
    blank code.

    Addresses, bank accounts, invoice lines and local paths are absent on
    purpose: this file is written to be attached to an e-mail. The paths are in
    this answer instead.

    Amounts are written exactly as KSeF stated them — no rounding, no float
    anywhere on the path — with a comma as the decimal separator and a semicolon
    between fields, which is what a Polish spreadsheet expects. Sums are in this
    answer, per currency, and never one figure across several of them.
    """
    with reported(AuditedOperation.STATEMENT) as journal:
        return export_statement(
            period=period,
            working_directory=working_directory,
            journal=journal,
        )
