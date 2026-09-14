from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import httpx
from ksef2 import Client, Environment
from ksef2.config import RetryConfig, TransportConfig
from ksef2.core.exceptions import (
    KSeFAuthError,
    KSeFException,
    KSeFRateLimitError,
    KSeFValidationError,
)
from ksef2.domain.models.invoices import InvoicesFilter
from ksef2.domain.models.pagination import InvoiceMetadataParams

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.errors import (
    KsefAuthenticationFailed,
    KsefRateLimited,
    KsefRefused,
    KsefUnreachable,
)
from ksef_mcp.ksef_port.types import (
    PAGE_SIZE,
    DateType,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    ExportStatus,
    InvoiceDirection,
    InvoiceMetadata,
    KsefLimits,
    KsefNumber,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
)

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
# Ministry reads as working around one. Waiting is `retry.RetryPolicy`'s job,
# above this layer, where the caller can see it happen.
SINGLE_ATTEMPT: Final[RetryConfig] = RetryConfig(max_attempts=1)

# A status code this high means the export will not finish, only that KSeF has
# an answer about why.
FAILED_EXPORT_CODE: Final[int] = 400

# What to spend against when KSeF answers about limits in a shape the SDK cannot
# parse. Production omits `collectiveIdentifier`, which `ksef2` requires without
# a default, so the whole limits response is lost over a field this project never
# reads (GH-76).
#
# `None` here would be the tempting shortcut and the wrong one: `QueryBudget`
# reads `per_hour is None` as "no ceiling" and then never refuses a call. A
# parse failure would silently disarm the counter, which is the pattern the
# Ministry blocks a subject for (D-020, D-031 §8). So these are real numbers,
# and they are the lowest ones observed rather than the most convenient.
CONSERVATIVE_RATES: Final[RateLimits] = RateLimits(
    metadata_queries=OperationLimit(per_second=8, per_minute=16, per_hour=20),
    exports=OperationLimit(per_second=2, per_minute=4, per_hour=20),
    export_statuses=OperationLimit(per_second=8, per_minute=16, per_hour=200),
    invoice_downloads=OperationLimit(per_second=4, per_minute=16, per_hour=64),
)

# Sizes, not rates (D-031 §9), and the documented allowance rather than a
# guess. Same direction of caution as above: a ceiling assumed too high is the
# one that costs a rejected session.
CONSERVATIVE_CEILINGS: Final[SessionCeilings] = SessionCeilings(
    max_invoice_megabytes=1,
    max_invoice_with_attachment_megabytes=3,
    max_invoices_per_session=10_000,
)

# A package part is tens of megabytes off presigned storage, so the default
# five seconds would abort a healthy download.
PART_DOWNLOAD_TIMEOUT: Final[float] = 300.0


@contextmanager
def translated() -> Iterator[None]:
    """Three inbound exception families, one outbound hierarchy."""
    try:
        yield
    except KSeFRateLimitError as error:
        raise KsefRateLimited(
            "KSeF refused the call: the hourly limit is spent.",
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
        raise KsefRefused(f"KSeF call failed: {error}") from error
    # Transport failures happen before a response exists, so the SDK lets httpx
    # errors through without a shared base class of its own.
    #
    # The class name, not `str(error)`: httpx spells the full request URL into
    # its message, and these messages now travel to the MCP client rather than
    # dying in a stderr nobody reads (GH-76). The class is the part that helps
    # — `ConnectTimeout` and `ConnectError` call for different answers — while
    # the URL only risks carrying whatever a future SDK version puts in a query
    # string. The original stays on `__cause__` for anyone reading a traceback.
    except httpx.HTTPError as error:
        raise KsefUnreachable(f"Could not reach KSeF: {type(error).__name__}") from error


def as_amount(value: float) -> Decimal:
    # Through the printed form, not the binary one: comparing an archive
    # against the register on binary floats invents differences of a grosz.
    return Decimal(str(value))


def as_filters(*, period: Period, direction: InvoiceDirection) -> InvoicesFilter:
    synchronising = period.date_type is DateType.PERMANENT_STORAGE
    return InvoicesFilter(
        role=direction.value,
        date_type=period.date_type.value,
        date_from=period.date_from,
        # The SDK has no way to omit the upper bound, so an open period becomes
        # "now" plus the HWM restriction: KSeF then still stops the package at
        # the point of completeness and picks the window itself (D-031 §4).
        date_to=period.date_to or datetime.now(tz=UTC),
        restrict_to_permanent_storage_hwm_date=synchronising or None,
    )


def as_metadata(record: object) -> InvoiceMetadata:
    # Metadata only. Invoice XML carries the counterparty's personal data and
    # never passes through this translation (D-011).
    return InvoiceMetadata(
        ksef_number=KsefNumber(record.ksef_number),
        seller_invoice_number=record.invoice_number,
        issue_date=record.issue_date,
        seller_nip=record.seller.nip,
        seller_name=record.seller.name,
        buyer_name=record.buyer.name,
        gross_amount=as_amount(record.gross_amount),
        net_amount=as_amount(record.net_amount),
        vat_amount=as_amount(record.vat_amount),
        currency=record.currency,
    )


def as_operation_limit(values: object) -> OperationLimit:
    return OperationLimit(
        per_second=values.per_second,
        per_minute=values.per_minute,
        per_hour=values.per_hour,
    )


def as_part(part: object) -> ExportPart:
    return ExportPart(
        ordinal=part.ordinal_number,
        name=part.part_name,
        method=part.method,
        url=str(part.url),
        size_bytes=part.part_size,
        content_hash=part.part_hash,
        encrypted_size_bytes=part.encrypted_part_size,
        encrypted_content_hash=part.encrypted_part_hash,
    )


def as_export_status(response: object) -> ExportStatus:
    package = response.package
    if package is None or not package.parts:
        return ExportStatus(
            state=(
                ExportState.FAILED
                if response.status.code >= FAILED_EXPORT_CODE
                else ExportState.RUNNING
            ),
            parts=(),
            truncated=False,
            hwm_date=None,
            last_permanent_storage_date=None,
            invoice_count=0,
        )
    return ExportStatus(
        state=ExportState.READY,
        parts=tuple(as_part(part) for part in package.parts),
        truncated=package.is_truncated,
        hwm_date=package.permanent_storage_hwm_date,
        last_permanent_storage_date=package.last_permanent_storage_date,
        invoice_count=package.invoice_count,
    )


@dataclass
class Ksef2Session:
    authenticated: object
    transport: httpx.Client

    def read_limits(self) -> KsefLimits:
        # Limits are a guard rail, not the errand. Every budget-counting tool
        # reads them first, so letting a schema mismatch out of here takes the
        # whole server down over a number nobody asked for (GH-76). Each read
        # falls back on its own: production can answer one of the two in a
        # shape we understand and the other not.
        rates, rates_read = self._read_rates()
        ceilings, ceilings_read = self._read_ceilings()
        return KsefLimits(
            rates=rates,
            ceilings=ceilings,
            degraded=not (rates_read and ceilings_read),
        )

    def _read_rates(self) -> tuple[RateLimits, bool]:
        with translated():
            try:
                rates = self.authenticated.limits.get_api_rate_limits()
            # Narrow on purpose. A payload we cannot parse is survivable; a
            # rejected token or an unreachable host is not, and those travel
            # the sibling branches of `translated()` untouched.
            except KSeFValidationError:
                return CONSERVATIVE_RATES, False
        return (
            RateLimits(
                metadata_queries=as_operation_limit(rates.invoice_metadata),
                exports=as_operation_limit(rates.invoice_export),
                export_statuses=as_operation_limit(rates.invoice_export_status),
                invoice_downloads=as_operation_limit(rates.invoice_download),
            ),
            True,
        )

    def _read_ceilings(self) -> tuple[SessionCeilings, bool]:
        with translated():
            try:
                ceilings = self.authenticated.limits.get_context_limits()
            except KSeFValidationError:
                return CONSERVATIVE_CEILINGS, False
        session = ceilings.online_session
        return (
            SessionCeilings(
                max_invoice_megabytes=session.max_invoice_size_mb,
                max_invoice_with_attachment_megabytes=session.max_invoice_with_attachment_size_mb,
                max_invoices_per_session=session.max_invoices,
            ),
            True,
        )

    def query_metadata(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
    ) -> MetadataPage:
        # Descending, because the SDK sorts ascending by default and the last
        # page is not the newest one — taking the head of page one would
        # quietly show the oldest invoices in the window. `page_size` is the
        # API ceiling and not a parameter anybody may tune (D-010).
        params = InvoiceMetadataParams(page_size=PAGE_SIZE, sort_order="desc")
        with translated():
            page = self.authenticated.invoices.query_metadata(
                filters=as_filters(period=period, direction=direction),
                params=params,
            )
        return MetadataPage(
            invoices=tuple(as_metadata(record) for record in page.invoices),
            has_more=page.has_more,
            truncated=page.is_truncated,
            hwm_date=page.permanent_storage_hwm_date,
        )

    def start_export(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
    ) -> ExportHandle:
        with translated():
            scheduled = self.authenticated.invoices.schedule_export(
                filters=as_filters(period=period, direction=direction),
            )
        # The key never goes to the keyring: it lives minutes to hours and is
        # worthless once the package is archived (D-033).
        return ExportHandle(
            reference=scheduled.reference_number,
            encryption=ExportEncryption(
                key=scheduled.aes_key,
                initialisation_vector=scheduled.iv,
            ),
        )

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        with translated():
            response = self.authenticated.invoices.get_export_status(
                reference_number=handle.reference,
            )
        return as_export_status(response)

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        # Still encrypted, deliberately. Decryption and unpacking belong to the
        # archive, which writes `temp → rename`; nothing here may reshape bytes
        # that a hash is later checked against.
        #
        # Straight over httpx rather than through the SDK: a package part comes
        # from presigned external storage, carries no KSeF credential, and the
        # SDK exposes that transport only behind a private attribute. Borrowing
        # a private is the kind of coupling this port exists to avoid.
        try:
            response = self.transport.request(part.method, part.url)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise KsefRefused(
                f"Storage refused part {part.ordinal} of export {handle.reference} "
                f"with {error.response.status_code}. A package link expires."
            ) from error
        except httpx.HTTPError as error:
            raise KsefUnreachable(f"Could not reach package storage: {error}") from error
        return response.content

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        with translated():
            return self.authenticated.invoices.download_invoice(
                ksef_number=ksef_number.value,
            )


@dataclass(frozen=True)
class Ksef2Port:
    environment: KsefEnvironment

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[Ksef2Session]:
        with (
            translated(),
            Client(
                environment=ENVIRONMENTS[self.environment],
                transport_config=TransportConfig(retry=SINGLE_ATTEMPT),
            ) as client,
            httpx.Client(timeout=PART_DOWNLOAD_TIMEOUT) as transport,
        ):
            authenticated = client.authentication.with_token(ksef_token=token, nip=nip)
            yield Ksef2Session(authenticated=authenticated, transport=transport)
