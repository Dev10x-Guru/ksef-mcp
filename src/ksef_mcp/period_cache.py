"""What a period answered last time, kept so the same question costs nothing.

The scarce resource is twenty metadata queries an hour, and the MCP server under
`uvx` is killed together with the agent session. Without a record on disk, the
second question about September is indistinguishable from the first and is paid
for twice — which is why D-021 rates this above deduplication: deduplication
saves disk, this saves the allowance.

Everything here lives under the platformdirs *cache* root, and that placement is
the load-bearing part (D-032). The cache convention reads "safe to delete at any
moment", and disk cleaners take it literally. So the two roots stay apart:

- cache root — this file, plus the marker of the last successful query. Losing
  it costs one re-query.
- data root — continuation points, the deduplication index, the archive. Losing
  those costs a full resynchronisation, and the symptom would surface long after
  the cleanup that caused it.

The same convention decides how a damaged entry is treated. `SyncStore` refuses
to guess at a record it cannot read, because a misread continuation point skips
invoices nothing asks for again. Here the honest answer to an unreadable entry
is to shrug and ask KSeF: the entry is reconstructible by definition, and one
query is exactly what its loss is supposed to cost.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from platformdirs import user_cache_path

from ksef_mcp.allowance import Allowance
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.budget import QueryBudget
from ksef_mcp.ksef_port.protocol import KsefSession
from ksef_mcp.ksef_port.types import (
    DateType,
    InvoiceMetadata,
    KsefNumber,
    MetadataPage,
    Operation,
    Period,
    SubjectRole,
)
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.paths import SubjectScope
from ksef_mcp.storage import exclusive_write, written_atomically

PERIOD_DIRECTORY: Final[str] = "periods"

CACHE_FILE_SUFFIX: Final[str] = ".json"

# Raised to 2 when a window's end stopped being nullable (GH-84), and to 3 when
# a page started carrying the offset it came from (GH-182). The version check is
# the searchable mechanism for "this entry predates a format change"; leaning on
# the decoder's exception instead would make a format change indistinguishable
# from a truncated file.
SCHEMA_VERSION: Final[int] = 3

# The completing loop stops while this many metadata queries are still unspent.
# One question covers four subject types (D-031 §5) out of one hourly allowance,
# so a window that drank the hour would leave the other three reported as
# unknown — and the reserve is the three that would otherwise go unasked.
METADATA_RESERVE: Final[int] = 3

CACHE_DIRECTORY_MODE: Final[int] = 0o700

# A metadata row names the counterparty (D-011), so the entry is created with
# its final mode rather than written and then tightened.
CACHE_FILE_MODE: Final[int] = 0o600

# Enough of the digest to make a collision between two windows unreachable,
# short enough to keep the name readable on Windows' path ceiling.
KEY_LENGTH: Final[int] = 32


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def cache_root() -> Path:
    # `user_cache_path`, the sibling of the `user_data_path` the archive and the
    # continuation points use — never a hand-assembled path, or Windows and
    # macOS inherit Linux's layout (D-032).
    return user_cache_path(appname=SERVER_NAME)


def is_cacheable(period: Period) -> bool:
    """A synchronisation window names no settled period, so no answer to it stays true.

    `restrict_to_permanent_storage_hwm_date` lets MF stop the package at the
    point of completeness, so what comes back is a function of the registry's
    state and not of the window alone. Remembering it would serve yesterday's
    invoices to tomorrow's question, and the question D-021 is about — "the same
    month again" — is never asked this way.

    Keyed on the date type, which is the predicate `as_filters` already uses, so
    the two agree about what a synchronisation window is instead of testing two
    different proxies for it.
    """
    return period.date_type is not DateType.PERMANENT_STORAGE


def cache_key(*, period: Period, subject_role: SubjectRole) -> str:
    """The identity of one answer: the window, how it was dated, and whose role.

    Per period *and* per subject type, because the same company is seller on one
    invoice and buyer on the next: one key for both would serve a buyer's answer
    to a seller's question and report a month as empty (D-031 §5).
    """
    spelling = f"{period.date_type}|{period.date_from.isoformat()}|{period.date_to.isoformat()}"
    digest = hashlib.sha256(spelling.encode("utf-8")).hexdigest()[:KEY_LENGTH]
    return f"{subject_role}-{digest}"


@dataclass(frozen=True)
class CachedPeriod:
    """One remembered answer, and when it was last paid for.

    `queried_at` is the marker T-06a asks for. It is kept beside the answer
    rather than in a register of its own: a marker without the answer it stands
    for would say a period was fetched while having nothing to show for it.
    """

    period: Period
    subject_role: SubjectRole
    page: MetadataPage
    queried_at: datetime


@dataclass(frozen=True)
class PeriodAnswer:
    """What the caller got, and whether KSeF was asked for it."""

    page: MetadataPage
    queried_at: datetime
    from_cache: bool


def _encode_invoice(invoice: InvoiceMetadata) -> dict[str, object]:
    # Amounts as strings: JSON floats are binary fractions, and a grosz lost to
    # rounding in a cached total is a discrepancy against the KSeF application
    # that nobody would think to blame on a cache.
    return {
        "ksef_number": str(invoice.ksef_number),
        "seller_invoice_number": invoice.seller_invoice_number,
        "issue_date": invoice.issue_date.isoformat(),
        "seller_nip": invoice.seller_nip,
        "seller_name": invoice.seller_name,
        "buyer_name": invoice.buyer_name,
        "gross_amount": str(invoice.gross_amount),
        "net_amount": str(invoice.net_amount),
        "vat_amount": str(invoice.vat_amount),
        "currency": invoice.currency,
    }


def _decode_invoice(stored: dict[str, object]) -> InvoiceMetadata:
    return InvoiceMetadata(
        ksef_number=KsefNumber(str(stored["ksef_number"])),
        seller_invoice_number=str(stored["seller_invoice_number"]),
        issue_date=date.fromisoformat(str(stored["issue_date"])),
        seller_nip=str(stored["seller_nip"]),
        seller_name=None if stored["seller_name"] is None else str(stored["seller_name"]),
        buyer_name=None if stored["buyer_name"] is None else str(stored["buyer_name"]),
        gross_amount=Decimal(str(stored["gross_amount"])),
        net_amount=Decimal(str(stored["net_amount"])),
        vat_amount=Decimal(str(stored["vat_amount"])),
        currency=str(stored["currency"]),
    )


def _encode_moment(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _decode_moment(stored: object) -> datetime | None:
    return None if stored is None else datetime.fromisoformat(str(stored))


def _decode_end(stored: object) -> datetime:
    """A window's end, refusing the entry rather than letting `None` travel.

    The schema bump above is what actually retires the entries that predate a
    required end. This stays as the backstop for a hand-edited or
    version-forged document, and says so instead of surfacing as an incidental
    `TypeError` from a comparison deeper in.
    """
    moment = _decode_moment(stored)
    if moment is None:
        raise ValueError("Cached window has no end; the entry predates GH-84.")
    return moment


def _encode(
    remembered: CachedPeriod,
    *,
    nip: str,
    environment: KsefEnvironment,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "nip": nip,
        "environment": str(environment),
        # The key keeps its old spelling, like the synchronisation record's: a
        # rename would refuse every entry written before this build, and an
        # entry refused is a metadata query spent again out of twenty an hour.
        "direction": str(remembered.subject_role),
        "queried_at": remembered.queried_at.isoformat(),
        "period": {
            "date_from": remembered.period.date_from.isoformat(),
            "date_to": remembered.period.date_to.isoformat(),
            "date_type": str(remembered.period.date_type),
        },
        "page": {
            "has_more": remembered.page.has_more,
            "truncated": remembered.page.truncated,
            "hwm_date": _encode_moment(remembered.page.hwm_date),
            "page_offset": remembered.page.page_offset,
            "budget_bound": remembered.page.budget_bound,
            "invoices": [_encode_invoice(invoice) for invoice in remembered.page.invoices],
        },
    }


def _decode(document: dict[str, object]) -> CachedPeriod:
    period: dict[str, object] = document["period"]  # type: ignore[assignment]
    page: dict[str, object] = document["page"]  # type: ignore[assignment]
    invoices: list[dict[str, object]] = page["invoices"]  # type: ignore[assignment]
    return CachedPeriod(
        period=Period(
            date_from=datetime.fromisoformat(str(period["date_from"])),
            date_to=_decode_end(period["date_to"]),
            date_type=DateType(period["date_type"]),
        ),
        subject_role=SubjectRole(document["direction"]),
        page=MetadataPage(
            invoices=tuple(_decode_invoice(invoice) for invoice in invoices),
            has_more=bool(page["has_more"]),
            truncated=bool(page["truncated"]),
            hwm_date=_decode_moment(page["hwm_date"]),
            page_offset=int(str(page["page_offset"])),
            budget_bound=bool(page["budget_bound"]),
        ),
        queried_at=datetime.fromisoformat(str(document["queried_at"])),
    )


@dataclass(frozen=True)
class PeriodCache:
    """One subject's remembered periods, in the cache root and nowhere else.

    Per subject and per environment for the same reason the archive is: a shared
    directory is the main vector for mixing an accounting office's clients
    (D-034), and a test answer served to a production question would report a
    month the taxpayer never filed against.
    """

    nip: str
    environment: KsefEnvironment
    root: Path | None = None
    clock: Callable[[], datetime] = now_utc

    @property
    def directory(self) -> Path:
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.cache_root(override=self.root) / PERIOD_DIRECTORY

    def path_for(self, *, period: Period, subject_role: SubjectRole) -> Path:
        key = cache_key(period=period, subject_role=subject_role)
        return self.directory / f"{key}{CACHE_FILE_SUFFIX}"

    def remembered(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
    ) -> CachedPeriod | None:
        path = self.path_for(period=period, subject_role=subject_role)
        if not path.is_file():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if document["schema_version"] != SCHEMA_VERSION:
                return None
            return _decode(document)
        except (OSError, ValueError, KeyError, TypeError):
            # A miss, never an error. This root is reconstructible by
            # construction, and refusing to answer because a cleaner truncated a
            # file would turn a saving into an outage. A corrupted KSeF number
            # or an impossible window is a damaged entry like any other and
            # arrives as a `ValueError`, which is the whole point of
            # `KsefMcpInputRejected` keeping that side: the tuple used to name
            # `KsefPortError` instead, and that root also covers
            # `KsefUnreachable` and `KsefAuthenticationFailed`, so an outage or
            # a refused login was answered as "not in the cache" (GH-170).
            return None

    def remember(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page: MetadataPage,
    ) -> CachedPeriod:
        """Stamp the answer with the moment it was paid for, and keep it on disk.

        The entry comes back either way, because the caller needs that moment to
        report; an open window simply leaves nothing behind for the next run.

        An incomplete page is not written. Nothing here expires, so remembering
        a window the allowance could not finish would freeze the part that fit
        as the answer forever — and the call that could finish it, an hour later
        with the allowance restored, would be served the stump from disk instead
        (GH-182).
        """
        entry = CachedPeriod(
            period=period,
            subject_role=subject_role,
            page=page,
            queried_at=self.clock(),
        )
        if not is_cacheable(period) or not page.complete:
            return entry
        path = self.path_for(period=period, subject_role=subject_role)
        document = _encode(entry, nip=self.nip, environment=self.environment)
        # temp → rename (D-006) here too. A half-written entry would be read as a
        # miss and cost the query this file exists to save, and the interrupted
        # write would have destroyed a good entry to do it.
        with exclusive_write(self.directory, directory_mode=CACHE_DIRECTORY_MODE):
            written_atomically(
                path,
                content=(json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                file_mode=CACHE_FILE_MODE,
            )
        return entry


@dataclass(frozen=True)
class PeriodMetadataReader:
    """Asks KSeF for a period once, and answers every repeat of it from disk.

    The budget is spent on the miss and only on the miss — that is the whole
    decision D-021 makes, expressed as the one place the allowance is touched.
    """

    cache: PeriodCache
    budget: QueryBudget

    def read(
        self,
        *,
        session: KsefSession,
        period: Period,
        subject_role: SubjectRole,
    ) -> PeriodAnswer:
        remembered = self.cache.remembered(period=period, subject_role=subject_role)
        if remembered is not None:
            return PeriodAnswer(
                page=remembered.page,
                queried_at=remembered.queried_at,
                from_cache=True,
            )
        # Spent before the call, not after: an answer that never arrives still
        # consumed the allowance, and a counter that only counts successes walks
        # the subject into the breach the Ministry logs (D-020).
        self.budget.spend(Operation.METADATA_QUERY)
        first = session.query_metadata(period=period, subject_role=subject_role, page_offset=0)
        whole = self._completed(
            session=session,
            period=period,
            subject_role=subject_role,
            page=first,
        )
        recorded = self.cache.remember(period=period, subject_role=subject_role, page=whole)
        return PeriodAnswer(page=whole, queried_at=recorded.queried_at, from_cache=False)

    def _completed(
        self,
        *,
        session: KsefSession,
        period: Period,
        subject_role: SubjectRole,
        page: MetadataPage,
    ) -> MetadataPage:
        """Fetch the rest of the window, one metadata query per further page.

        To completion rather than to a page count, because a count is a guessed
        number and the persistent ledger already knows the true bound (ADR-109).
        What stops the loop is therefore the allowance, and the page that comes
        back says so: `has_more` with `budget_bound` is a window worth asking
        about again, `has_more` alone is one KSeF itself cut short.
        """
        gathered = list(page.invoices)
        while page.has_more:
            if not self._affordable():
                return replace(page, invoices=tuple(gathered), budget_bound=True)
            self.budget.spend(Operation.METADATA_QUERY)
            page = session.query_metadata(
                period=period,
                subject_role=subject_role,
                page_offset=page.page_offset + 1,
            )
            gathered.extend(page.invoices)
        return replace(page, invoices=tuple(gathered))

    def _affordable(self) -> bool:
        # Unknown headroom counts as unaffordable. A counter that cannot refuse
        # would let a server answering `has_more` forever turn this loop into
        # the sustained hammering the Ministry blocks subjects for (D-020).
        left = self.budget.remaining(Operation.METADATA_QUERY)
        return left is not None and left > METADATA_RESERVE

    def page_for(
        self,
        *,
        session: KsefSession,
        period: Period,
        subject_role: SubjectRole,
    ) -> MetadataPage:
        """The `PeriodReader` the port asks for, over the answer this class gives.

        A caller that only wants the invoices should not have to know whether
        they were paid for — but `check_connection` lives inside the port and
        cannot name `PeriodAnswer`, so the narrow half is spelled out here.
        """
        return self.read(session=session, period=period, subject_role=subject_role).page


@dataclass(frozen=True)
class MeteredPeriods:
    """The remembered answers and the counter that pays for the misses, paired.

    What `check_connection` is handed instead of a bare session. The pairing is
    the point: a cache without a counter gives away the allowance silently, and
    a counter without a cache charges for a question already answered (D-021).
    """

    cache: PeriodCache
    allowance: Allowance

    def reader_for(self, *, session: KsefSession) -> PeriodMetadataReader:
        return PeriodMetadataReader(
            cache=self.cache,
            budget=self.allowance.budget(session=session),
        )

    def guarded(self, *, session: KsefSession) -> KsefSession:
        return self.allowance.guarded(session=session)
