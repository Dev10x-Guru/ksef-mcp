from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

import httpx
from ksef2 import Client, Environment
from ksef2.config import RetryConfig, TransportConfig
from ksef2.core.exceptions import KSeFAuthError, KSeFException, KSeFRateLimitError
from ksef2.domain.models.invoices import InvoiceMetadata, InvoicesFilter
from ksef2.domain.models.pagination import InvoiceMetadataParams

from ksef_mcp.config import KsefEnvironment

# The SDK constructor defaults to PRODUCTION. Every call site here passes the
# environment explicitly so that a forgotten argument cannot reach the live
# registry — the project treats that as non-negotiable.
ENVIRONMENTS: Final[dict[KsefEnvironment, Environment]] = {
    KsefEnvironment.TEST: Environment.TEST,
    KsefEnvironment.DEMO: Environment.DEMO,
    KsefEnvironment.PRODUCTION: Environment.PRODUCTION,
}

# The SDK retries 429 on its own: three attempts, backoff capped at four
# seconds. A real KSeF Retry-After is measured in minutes, so that loop can
# never outlast an actual limit — it only adds attempts to a pattern the
# Ministry reads as working around one, and the block lengthens on repeats.
# One attempt, then hand the waiting decision to the person.
SINGLE_ATTEMPT: Final[RetryConfig] = RetryConfig(max_attempts=1)

# The API floor is 10; asking for fewer is rejected. Fetch a page, show a few.
PAGE_SIZE: Final[int] = 10

DEFAULT_WINDOW: Final[timedelta] = timedelta(days=30)

DEFAULT_LIMIT: Final[int] = 5


class KsefPortError(RuntimeError):
    pass


class KsefAuthenticationFailed(KsefPortError):
    pass


class KsefRateLimited(KsefPortError):
    def __init__(self, message: str, *, retry_after: int | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class KsefUnreachable(KsefPortError):
    pass


@dataclass(frozen=True)
class InvoiceSummary:
    ksef_number: str
    issue_date: date
    seller_name: str | None
    seller_nip: str
    gross_amount: float
    currency: str


@dataclass(frozen=True)
class ConnectionCheck:
    environment: KsefEnvironment
    subject_name: str | None
    invoices: tuple[InvoiceSummary, ...]


def summarize(metadata: InvoiceMetadata) -> InvoiceSummary:
    # Metadata only. Invoice XML carries the counterparty's personal data and
    # never passes through this layer (D-011).
    return InvoiceSummary(
        ksef_number=metadata.ksef_number,
        issue_date=metadata.issue_date,
        seller_name=metadata.seller.name,
        seller_nip=metadata.seller.nip,
        gross_amount=metadata.gross_amount,
        currency=metadata.currency,
    )


def check_connection(
    *,
    nip: str,
    token: str,
    environment: KsefEnvironment,
    window: timedelta = DEFAULT_WINDOW,
    limit: int = DEFAULT_LIMIT,
) -> ConnectionCheck:
    filters = InvoicesFilter.for_buyer(date_from=datetime.now(tz=UTC) - window)
    # Descending, because the SDK sorts ascending by default and the last page
    # is not the newest one — taking the tail of page one would quietly show
    # the oldest invoices in the window.
    params = InvoiceMetadataParams(page_size=PAGE_SIZE, sort_order="desc")
    try:
        with Client(
            environment=ENVIRONMENTS[environment],
            transport_config=TransportConfig(retry=SINGLE_ATTEMPT),
        ) as client:
            authenticated = client.authentication.with_token(ksef_token=token, nip=nip)
            page = authenticated.invoices.query_metadata(filters=filters, params=params)
    except KSeFRateLimitError as error:
        raise KsefRateLimited(
            "KSeF refused the query: the hourly limit is spent.",
            retry_after=error.retry_after,
        ) from error
    # The NIP stays out of the message: an exception outlives the terminal it
    # was raised in, and the caller has already printed which subject it used.
    except KSeFAuthError as error:
        raise KsefAuthenticationFailed(
            "KSeF rejected the token for the configured subject. Check the NIP "
            "and whether the token carries the InvoiceRead entitlement."
        ) from error
    except KSeFException as error:
        raise KsefUnreachable(f"KSeF call failed: {error}") from error
    # Transport failures happen before a response exists, so the SDK lets
    # httpx errors through without a shared base class of its own.
    except httpx.HTTPError as error:
        raise KsefUnreachable(f"Could not reach KSeF: {error}") from error
    found = page.invoices[:limit]
    return ConnectionCheck(
        environment=environment,
        subject_name=found[0].buyer.name if found else None,
        invoices=tuple(summarize(item) for item in found),
    )
