"""One month as one file, in the directory the taxpayer declared as theirs.

The chat window is where an answer is read; it is not where an answer goes.
A month reconciled in a conversation has to be re-typed to reach the place the
accountant's own work lives, and re-typing a hundred and thirty amounts is how a
grosz goes missing. So this module ends the month in a file that can be attached
to an e-mail, and the whole design follows from that file leaving the machine.

Leaving the machine decides the columns. The eight settled in #41 are the
reconciliation minimum — the KSeF number, the seller's own number, the date, the
counterparty's NIP and name, and the three amounts — with the currency added in
#63, because a month mixing euro and złoty otherwise summed into one figure that
meant nothing. Addresses, bank accounts and
invoice lines are absent by choice, not by omission: a CSV is the artefact most
likely to end up in somebody's cloud spreadsheet, so it carries what a
reconciliation needs and not one field more. Local paths are absent for a second
reason — they are useless to whoever opens the attachment, and the tool's own
answer already names them (D-011).

Leaving the machine also decides where the file lands. Neither the cache root
nor the data root: those are internal storage, and this is a product (D-032).
The working directory is the one the taxpayer named at onboarding, per subject,
`0700`, and a path that looks like a sync client's folder is called out rather
than silently used. A working directory inside either internal root is refused
outright — otherwise "deleting the statements does not touch the archive" is an
intention rather than a property.

The verification code is composed here rather than fetched. Its three parts —
the seller's NIP, the issue date, and the SHA-256 of the archived invoice — are
the same three the Ministry's verification path is built from, and the digest
comes from `archive.digest_of` over the file the archive actually holds, so the
code stands for the bytes on disk and not for a metadata row about them. It is
the code and not a portal address: a verification link is a bearer credential
(D-012), and this file is written to be forwarded.
"""

from __future__ import annotations

import csv
import io
import re
from calendar import monthrange
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Self

from platformdirs import user_data_path

from ksef_mcp.allowance import Allowance
from ksef_mcp.archive import (
    INVOICE_SUFFIX,
    ArchiveIndexUnreadable,
    InvoiceArchive,
    digest_of,
)
from ksef_mcp.config import (
    INVOICE_DIRECTORY_MODE,
    KsefEnvironment,
    cloud_sync_marker,
    prepare_invoice_directory,
)
from ksef_mcp.errors import KsefMcpError, KsefMcpInputRejected
from ksef_mcp.ksef_port.protocol import KsefPort
from ksef_mcp.ksef_port.types import (
    Credential,
    DateType,
    InvoiceMetadata,
    Period,
    SubjectRole,
)
from ksef_mcp.listing import CurrencyTotal, gross_totals, invoices_phrase
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.period_cache import PeriodCache, PeriodMetadataReader, cache_root
from ksef_mcp.storage import written_atomically

# The counterparty on every one of the eight columns is the seller, so the
# querying subject is the buyer: this is the purchase side of the month, the one
# an accountant reconciles against what the taxpayer was invoiced.
STATEMENT_SUBJECT_ROLE: Final[SubjectRole] = SubjectRole.BUYER

STATEMENT_PREFIX: Final[str] = "zestawienie"

STATEMENT_SUFFIX: Final[str] = ".csv"

# Upper case and in the recipient's language, because it has one job: to be
# read in a mailbox, by somebody who did not run the tool.
STATEMENT_INCOMPLETE_MARK: Final[str] = "NIEKOMPLETNE"

# The rows name counterparties, so the file is created with its final mode
# rather than written and then tightened (D-011).
STATEMENT_FILE_MODE: Final[int] = 0o600

# Polish Excel reads a semicolon as the list separator and a comma as the
# decimal point. A file the accountant has to re-import through a dialog is a
# file that will be re-typed instead.
CSV_DELIMITER: Final[str] = ";"

CSV_LINE_TERMINATOR: Final[str] = "\r\n"

DECIMAL_SEPARATOR: Final[str] = ","

# Without it Excel reads UTF-8 as the local code page and the counterparty names
# arrive mangled.
BYTE_ORDER_MARK: Final[str] = "﻿"

COLUMNS: Final[tuple[str, ...]] = (
    "Numer KSeF",
    "Numer faktury sprzedawcy",
    "Data wystawienia",
    "NIP sprzedawcy",
    "Nazwa sprzedawcy",
    "Brutto",
    "Netto",
    "VAT",
    # Next to the amounts it qualifies rather than at the end: a column that
    # says which currency a figure is in is useless one scroll away from it.
    # The eight reconciliation columns settled in #41 carried no currency, so
    # a month mixing euro and złoty summed into one meaningless total (#63).
    "Waluta",
    "KOD I",
)

# An empty cell would read as an invoice without a code. This says which of the
# two it is: the invoice body is not in the archive yet.
NO_ARCHIVED_BODY: Final[str] = "brak pliku w archiwum"

MONTH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class UnreadablePeriod(KsefMcpInputRejected):
    """The period is not a month this tool can turn into a date window."""


class WorkingDirectoryRefused(KsefMcpError):
    """The declared working directory is internal storage, not a product directory."""


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def amount(value: Decimal) -> str:
    """Exactly the decimal that came from KSeF, spelled the way Excel PL reads it.

    `format(value, "f")` never falls back to exponent notation and never rounds,
    so nothing between the port and the file can cost a grosz. A float anywhere
    on this path would.
    """
    return format(value, "f").replace(".", DECIMAL_SEPARATOR)


@dataclass(frozen=True)
class AccountingPeriod:
    """A calendar month — the unit an accountant closes, not an arbitrary window."""

    year: int
    month: int

    @classmethod
    def parsed(cls, spelling: str) -> Self:
        matched = MONTH_PATTERN.match(spelling)
        if matched is None:
            raise UnreadablePeriod(
                f"Nie rozumiem okresu {spelling!r}. Miesiąc zapisujemy jako "
                f"RRRR-MM, na przykład 2026-08."
            )
        return cls(year=int(matched.group(1)), month=int(matched.group(2)))

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def queried(self) -> Period:
        """Both ends closed, so the cache can match the question and answer it for free.

        Dated by issue date, because that is the date the eight columns carry and
        the one a month is assigned by; the storage date would put an invoice in
        the month KSeF finished processing it rather than the month it belongs to.
        """
        last_day = monthrange(self.year, self.month)[1]
        return Period(
            date_from=datetime(self.year, self.month, 1, tzinfo=UTC),
            date_to=datetime(self.year, self.month, last_day, 23, 59, 59, 999999, tzinfo=UTC),
            date_type=DateType.ISSUE,
        )


@dataclass(frozen=True)
class VerificationCode:
    """KOD I: whose invoice, from when, and the digest of the bytes we hold."""

    seller_nip: str
    issue_date: date
    content_hash: str

    def __str__(self) -> str:
        return f"{self.seller_nip}-{self.issue_date.strftime('%Y%m%d')}-{self.content_hash}"


def archived_digests(archive: InvoiceArchive) -> dict[str, str]:
    """Every digest the deduplication index already holds, read once for the month.

    An unreadable index answers with nothing rather than refusing the statement.
    The index is this module's shortcut and not its source of truth — the files
    are — so a damaged one costs the reads it was meant to save and nothing more.
    Refusing to hand an accountant her month over a helper file would be the
    worse failure by far.
    """
    try:
        return {entry.ksef_number: entry.content_hash for entry in archive.load_index().entries}
    except ArchiveIndexUnreadable:
        return {}


def verification_code(
    *,
    invoice: InvoiceMetadata,
    archive: InvoiceArchive,
    digests: Mapping[str, str],
) -> VerificationCode | None:
    """The digest of the archived body, or say there is none. Never the metadata row.

    A digest over a metadata row would verify our own bookkeeping against itself.
    The file the archive holds is the thing a verification is about — but the
    index already recorded that file's digest when it was written, so a month of
    four hundred invoices no longer means four hundred whole XML documents read
    off the disk to state what is already known (GH-148).

    The full read stays underneath as the fallback, because the index can be
    older than the directory it describes and a code is a claim about the bytes
    on disk. The existence of the body is still checked against the disk and
    never against the index, for the same reason.
    """
    body = archive.invoice_directory / f"{invoice.ksef_number}{INVOICE_SUFFIX}"
    if not body.is_file():
        return None
    recorded = digests.get(str(invoice.ksef_number))
    return VerificationCode(
        seller_nip=invoice.seller_nip,
        issue_date=invoice.issue_date,
        content_hash=digest_of(body.read_bytes()) if recorded is None else recorded,
    )


@dataclass(frozen=True)
class StatementRow:
    invoice: InvoiceMetadata
    code: VerificationCode | None

    @property
    def cells(self) -> tuple[str, ...]:
        return (
            str(self.invoice.ksef_number),
            self.invoice.seller_invoice_number,
            self.invoice.issue_date.isoformat(),
            self.invoice.seller_nip,
            "" if self.invoice.seller_name is None else self.invoice.seller_name,
            amount(self.invoice.gross_amount),
            amount(self.invoice.net_amount),
            amount(self.invoice.vat_amount),
            self.invoice.currency,
            NO_ARCHIVED_BODY if self.code is None else str(self.code),
        )


def rows_for(
    *,
    invoices: tuple[InvoiceMetadata, ...],
    archive: InvoiceArchive,
) -> tuple[StatementRow, ...]:
    digests = archived_digests(archive)
    return tuple(
        StatementRow(
            invoice=invoice,
            code=verification_code(invoice=invoice, archive=archive, digests=digests),
        )
        for invoice in invoices
    )


def rendered(rows: tuple[StatementRow, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator=CSV_LINE_TERMINATOR)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(row.cells)
    return BYTE_ORDER_MARK + buffer.getvalue()


def statement_file_name(
    *,
    period: AccountingPeriod,
    nip: str,
    complete: bool = True,
) -> str:
    """A name that says what the attachment is once it is out of its directory.

    The word first, because the recipient sees it in a mailbox and not in the
    working directory. Then the month, so a year of them sorts chronologically.
    Then the NIP, because the directory is per subject and the file stops being
    in it the moment it is attached to anything.

    An incomplete period says so in the name. Everything else this module knows
    about the shortfall stays in the tool's answer, which the accountant never
    sees — the file is what gets forwarded, so the caveat has to travel on the
    file (GH-181).
    """
    mark = "" if complete else f"-{STATEMENT_INCOMPLETE_MARK}"
    return f"{STATEMENT_PREFIX}-{period}-{nip}{mark}{STATEMENT_SUFFIX}"


def internal_root_conflict(
    directory: Path,
    *,
    data_root: Path,
    cache_root_path: Path,
) -> Path | None:
    """Which internal root the declared directory falls inside, if any (D-032)."""
    resolved = directory.expanduser().resolve()
    return next(
        (
            root
            for root in (data_root, cache_root_path)
            if resolved == root.resolve() or resolved.is_relative_to(root.resolve())
        ),
        None,
    )


@dataclass(frozen=True)
class WorkingDirectory:
    """A product directory, made ready and described rather than silently used."""

    path: Path
    created: bool
    mode: int
    cloud_marker: str | None

    @property
    def warnings(self) -> tuple[str, ...]:
        told: list[str] = []
        if self.cloud_marker is not None:
            told.append(
                f"Katalog roboczy wygląda na synchronizowany do chmury "
                f"({self.cloud_marker}). Dokument nazywa kontrahentów — "
                f"kopia trafi na cudzy serwer."
            )
        if self.mode != INVOICE_DIRECTORY_MODE:
            told.append(
                f"Katalog roboczy istniał wcześniej i ma uprawnienia "
                f"{self.mode:04o}, nie zmieniam ich. Jeśli ma być prywatny: "
                f"chmod 0700 {self.path}"
            )
        return tuple(told)


def prepare_working_directory(
    directory: Path,
    *,
    data_root: Path | None = None,
    cache_root_path: Path | None = None,
) -> WorkingDirectory:
    """Refuse internal storage, then create the directory `0700` if it is new."""
    resolved_data = user_data_path(appname=SERVER_NAME) if data_root is None else data_root
    resolved_cache = cache_root() if cache_root_path is None else cache_root_path
    conflict = internal_root_conflict(
        directory,
        data_root=resolved_data,
        cache_root_path=resolved_cache,
    )
    if conflict is not None:
        raise WorkingDirectoryRefused(
            f"Katalog roboczy {directory} leży wewnątrz {conflict}, a to magazyn "
            f"wewnętrzny — archiwum albo cache (D-032). Skasowanie zestawień "
            f"zabrałoby stamtąd faktury albo punkty kontynuacji. Wskaż katalog "
            f"poza oboma korzeniami."
        )
    prepared = prepare_invoice_directory(directory)
    return WorkingDirectory(
        path=prepared.path,
        created=prepared.created,
        mode=prepared.mode,
        cloud_marker=cloud_sync_marker(prepared.path),
    )


def write_statement(*, rows: tuple[StatementRow, ...], path: Path) -> Path:
    """Staging file then rename, so an interrupted write never looks like a month.

    The line terminator is stated by the writer rather than left to the stream,
    so the rendered text is already exactly the bytes the file should hold.
    """
    return written_atomically(
        path,
        content=rendered(rows).encode("utf-8"),
        file_mode=STATEMENT_FILE_MODE,
    )


@dataclass(frozen=True)
class Statement:
    """What was written and what the reader has to know before trusting the sum."""

    nip: str
    environment: KsefEnvironment
    period: AccountingPeriod
    path: str
    row_count: int
    gross_totals: tuple[CurrencyTotal, ...]
    complete: bool
    budget_bound: bool
    from_cache: bool
    queried_at: datetime
    warnings: tuple[str, ...]
    # Which invoices the file names, for the audit trail and for nothing else
    # (#45). Deliberately absent from the tool's answer: fifty numbers in a chat
    # window are noise, while a record of the access that cannot say what was
    # read is not a record at all.
    ksef_numbers: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return (
            f"Zestawienie za {self.period}: {invoices_phrase(self.row_count)}, "
            f"brutto {', '.join(str(total) for total in self.gross_totals) or '—'}. "
            f"{self.shortfall}Plik: {self.path}"
        )

    @property
    def shortfall(self) -> str:
        """The caveat, in the sentence that carries the number rather than beside it.

        The product of this tool is a figure a person forwards to an accountant,
        and a sum counted from part of a month looks exactly like a whole one. A
        warning in a separate list is read after the decision, if at all
        (GH-181).
        """
        if self.complete:
            return ""
        if self.budget_bound:
            return (
                "UWAGA: to nie jest cały okres — skończył się godzinowy przydział "
                "zapytań, więc suma obejmuje część dokumentów. Ponów za godzinę, "
                "zanim to wyślesz. "
            )
        return (
            "UWAGA: to nie jest cały okres — KSeF nie oddał go w całości, więc "
            "suma obejmuje część dokumentów. "
        )


def completeness_warning(*, complete: bool, budget_bound: bool = False) -> tuple[str, ...]:
    if complete:
        return ()
    # D-023's rule, and it matters more here than in a chat window: a CSV keeps
    # no room for a caveat, so the caveat has to travel in the answer. Which of
    # the two shortfalls it was decides what the reader should do about it.
    if budget_bound:
        return (
            "Nie dociągnąłem całego okresu — skończył się godzinowy przydział "
            "zapytań o metadane. Zestawienie nie jest kompletem, a suma nie "
            "uzgodni się z Aplikacją Podatnika. Ponów za godzinę.",
        )
    return (
        "KSeF nie oddał całego okresu — zestawienie nie jest kompletem i suma "
        "nie uzgodni się z Aplikacją Podatnika.",
    )


def currency_warning(totals: tuple[CurrencyTotal, ...]) -> tuple[str, ...]:
    if len(totals) < 2:
        return ()
    # The "Waluta" column tells the rows apart (#63), but a spreadsheet summing
    # "Brutto" whole still adds euro to złoty. Naming the currency removes the
    # ambiguity from the file; it does not make the naive total correct.
    return (
        f"Okres ma faktury w kilku walutach ({', '.join(total.currency for total in totals)}). "
        f"Kolumna Waluta rozróżnia wiersze, ale suma całej kolumny Brutto "
        f"dodałaby waluty do siebie — sumuj po walutach.",
    )


def unverifiable_warning(rows: tuple[StatementRow, ...]) -> tuple[str, ...]:
    missing = sum(1 for row in rows if row.code is None)
    if missing == 0:
        return ()
    return (
        f"Bez KOD I: {missing} z {len(rows)} pozycji — tych faktur nie ma w "
        f"archiwum. Uruchom synchronizację i złóż zestawienie ponownie.",
    )


@dataclass(frozen=True)
class StatementComposer:
    """Reads one month for one subject role and leaves it as a file on disk.

    One metadata query per month, routed through the period cache, so composing
    the same statement twice — which is what happens when the first attempt is
    e-mailed to the wrong address — costs nothing from twenty an hour (D-021).
    """

    port: KsefPort
    cache: PeriodCache
    archive: InvoiceArchive
    allowance: Allowance
    clock: Callable[[], datetime] = now_utc

    def run(
        self,
        *,
        nip: str,
        token: Credential,
        period: AccountingPeriod,
        directory: Path,
    ) -> Statement:
        working = prepare_working_directory(directory)
        window = period.queried
        with self.port.session(nip=nip, token=token) as opened:
            session = self.allowance.guarded(session=opened)
            reader = PeriodMetadataReader(
                cache=self.cache,
                budget=self.allowance.budget(session=session),
            )
            answer = reader.read(
                session=session,
                period=window,
                subject_role=STATEMENT_SUBJECT_ROLE,
            )
        rows = rows_for(invoices=answer.page.invoices, archive=self.archive)
        totals = gross_totals(answer.page.invoices)
        complete = answer.page.complete
        budget_bound = answer.page.budget_bound
        path = write_statement(
            rows=rows,
            path=working.path / statement_file_name(period=period, nip=nip, complete=complete),
        )
        return Statement(
            nip=nip,
            environment=self.port.environment,
            period=period,
            path=str(path),
            row_count=len(rows),
            gross_totals=totals,
            complete=complete,
            budget_bound=budget_bound,
            from_cache=answer.from_cache,
            queried_at=answer.queried_at,
            warnings=(
                *working.warnings,
                *completeness_warning(complete=complete, budget_bound=budget_bound),
                *currency_warning(totals),
                *unverifiable_warning(rows),
            ),
            ksef_numbers=tuple(str(row.invoice.ksef_number) for row in rows),
        )
