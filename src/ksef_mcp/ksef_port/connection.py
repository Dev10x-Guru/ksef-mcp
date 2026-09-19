from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.protocol import KsefPort, KsefSession
from ksef_mcp.ksef_port.types import (
    Credential,
    DateType,
    InvoiceDirection,
    InvoiceMetadata,
    MetadataPage,
    Period,
)

DEFAULT_WINDOW: Final[timedelta] = timedelta(days=30)

DEFAULT_LIMIT: Final[int] = 5


class PeriodReader(Protocol):
    """Whatever can answer a window, and charge the allowance when it has to.

    Narrow on purpose, and returning a `MetadataPage` rather than the richer
    answer its implementation has: the implementation lives in the application
    layer, and a protocol naming that layer's types would turn this boundary
    inside out (ADR-102).
    """

    def page_for(
        self,
        *,
        session: KsefSession,
        period: Period,
        direction: InvoiceDirection,
    ) -> MetadataPage:
        """Answer the window, from disk when it can and from KSeF when it must."""


class PeriodReaders(Protocol):
    """Hands out a reader once there is a session to count against.

    A factory rather than a ready-made reader, because the allowance a counter
    spends against is read from the open session: KSeF states the ceilings for
    the authenticated context, and a counter built before authentication would
    have to assume them (D-031 §8).
    """

    def reader_for(self, *, session: KsefSession) -> PeriodReader:
        """The reader for this session, counting against what KSeF granted it."""

    def guarded(self, *, session: KsefSession) -> KsefSession:
        """The session with the local fuse in front of it (GH-99)."""


def check_period(*, moment: datetime, window: timedelta = DEFAULT_WINDOW) -> Period:
    """The window the check asks about — ending on the hour, so a repeat is free.

    Both ends stated, from one reading of the clock. An unstated end is not an
    unbounded one — the adapter sends "now" in its place — so leaving it unset
    only moved the window past the ceiling check (GH-84). Thirty days clears
    that ceiling today; saying so explicitly is what keeps a caller's wider
    `window` from repeating the same slip.

    Rounded down to the hour for the reason `listing_period` is: an end taken
    raw from the wall clock differs by microseconds between two runs, so every
    `verify` would be a fresh identity and the cache would never match one
    (D-021). Somebody diagnosing a connection runs this command several times in
    a row, which is exactly the case the rounding pays for.
    """
    ends = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return Period(date_from=ends - window, date_to=ends, date_type=DateType.ISSUE)


@dataclass(frozen=True)
class ConnectionCheck:
    environment: KsefEnvironment
    subject_name: str | None
    invoices: tuple[InvoiceMetadata, ...]


def check_connection(
    *,
    port: KsefPort,
    nip: str,
    token: Credential,
    readers: PeriodReaders,
    window: timedelta = DEFAULT_WINDOW,
    limit: int = DEFAULT_LIMIT,
) -> ConnectionCheck:
    """One authentication and one metadata query — the cheapest proof of life.

    Deliberately the smallest thing the port can do: confirming the token works
    must not cost an export from an allowance of twenty per hour.

    The `readers` argument is not optional, and that is the whole of GH-98. This
    function used to call `query_metadata` straight through, outside both the
    counter and the cache — so `ksef-mcp verify`, which a person runs repeatedly
    precisely when something is already wrong, fed uncounted requests to the
    very allowance whose exhaustion earns a block. A default that skipped them
    would be the same bypass with a shorter call site.
    """
    period = check_period(moment=datetime.now(tz=UTC), window=window)
    with port.session(nip=nip, token=token) as opened:
        session = readers.guarded(session=opened)
        page = readers.reader_for(session=session).page_for(
            session=session,
            period=period,
            direction=InvoiceDirection.BUYER,
        )
    found = page.invoices[:limit]
    return ConnectionCheck(
        environment=port.environment,
        subject_name=found[0].buyer_name if found else None,
        invoices=found,
    )
