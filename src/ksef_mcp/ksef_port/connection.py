from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.protocol import KsefPort
from ksef_mcp.ksef_port.types import DateType, InvoiceDirection, InvoiceMetadata, Period

DEFAULT_WINDOW: Final[timedelta] = timedelta(days=30)

DEFAULT_LIMIT: Final[int] = 5


@dataclass(frozen=True)
class ConnectionCheck:
    environment: KsefEnvironment
    subject_name: str | None
    invoices: tuple[InvoiceMetadata, ...]


def check_connection(
    *,
    port: KsefPort,
    nip: str,
    token: str,
    window: timedelta = DEFAULT_WINDOW,
    limit: int = DEFAULT_LIMIT,
) -> ConnectionCheck:
    """One authentication and one metadata query — the cheapest proof of life.

    Deliberately the smallest thing the port can do: confirming the token works
    must not cost an export from an allowance of twenty per hour.
    """
    period = Period(
        date_from=datetime.now(tz=UTC) - window,
        date_to=None,
        date_type=DateType.ISSUE,
    )
    with port.session(nip=nip, token=token) as session:
        page = session.query_metadata(period=period, direction=InvoiceDirection.BUYER)
    found = page.invoices[:limit]
    return ConnectionCheck(
        environment=port.environment,
        subject_name=found[0].buyer_name if found else None,
        invoices=found,
    )
