from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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

from ksef_mcp.diagnostics import technical_log
from ksef_mcp.ksef_port.errors import (
    KsefAuthenticationFailed,
    KsefRateLimited,
    KsefRefused,
    KsefUnreachable,
    PackageLinkExpired,
)
from ksef_mcp.ksef_port.types import (
    PAGE_SIZE,
    Credential,
    DateType,
    DocumentType,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    ExportStatus,
    InvoiceMetadata,
    KsefEnvironment,
    KsefLimits,
    KsefNumber,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
    SubjectRole,
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

# How presigned storage spells "this signature is no longer valid". 403 is what
# S3-shaped storage answers once the expiry stamped into the URL has passed; 410
# is the same statement made explicitly. Neither heals by waiting, so they are
# the two the caller must be able to tell apart from an outage (GH-93).
EXPIRED_LINK_STATUSES: Final[frozenset[int]] = frozenset({403, 410})


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


def as_filters(*, period: Period, subject_role: SubjectRole) -> InvoicesFilter:
    synchronising = period.date_type is DateType.PERMANENT_STORAGE
    return InvoicesFilter(
        role=subject_role.value,
        date_type=period.date_type.value,
        date_from=period.date_from,
        # Both ends come from the `Period`, and neither is invented here. This
        # line used to read `period.date_to or datetime.now(tz=UTC)`, which is
        # where the hundred-day window acquired the end the ceiling had never
        # measured (GH-84). The SDK has no way to omit an upper bound anyway, so
        # the substitution bought nothing and hid the span; the HWM restriction
        # below is what actually lets KSeF stop the package where it likes
        # (D-031 §4).
        date_to=period.date_to,
        restrict_to_permanent_storage_hwm_date=synchronising or None,
    )


def as_document_type(value: object) -> DocumentType:
    """The SDK's invoice type, or `UNKNOWN` when it is one we have not met.

    Lenient on purpose. The type is read so a statement can warn about
    corrections, and a month that raises `ValueError` because the Ministry
    added a thirteenth type would trade a missing warning for a missing
    statement.
    """
    try:
        return DocumentType(value)
    except ValueError:
        return DocumentType.UNKNOWN


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
        document_type=as_document_type(record.invoice_type),
        content_hash=record.invoice_hash,
        corrected_content_hash=record.hash_of_corrected_invoice,
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


def as_unbuilt_state(response: object) -> ExportState:
    """What an export with no parts is, once KSeF has been asked about it.

    `completedDate` is the only field in the SDK's response model that states
    KSeF is done with an export — `ExportStatusInfo` carries a bare `int` and a
    description, and the SDK enumerates no export states at all. Reading it is
    what tells a closed empty window apart from a package still being built
    (GH-190), and both arrive here looking identical: no package, or a package
    whose part list is empty, which the model permits in either state.

    The date and not a threshold on the code. A code above two hundred is the
    convention the SDK follows for batches, but for exports it is inference,
    and calling a live export finished skips the invoices it was still
    gathering — which nothing asks for again.
    """
    if response.status.code >= FAILED_EXPORT_CODE:
        return ExportState.FAILED
    return ExportState.RUNNING if response.completed_date is None else ExportState.EMPTY


def as_export_status(response: object) -> ExportStatus:
    package = response.package
    if package is None or not package.parts:
        return ExportStatus(
            state=as_unbuilt_state(response),
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
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        # Descending, because the SDK sorts ascending by default and the last
        # page is not the newest one — taking the head of page one would
        # quietly show the oldest invoices in the window. `page_size` is the
        # API ceiling and not a parameter anybody may tune (D-010).
        params = InvoiceMetadataParams(page_size=PAGE_SIZE, sort_order="desc").with_page_offset(
            page_offset
        )
        # Every KSeF request this port makes leaves a line carrying the
        # correlation identifier the tool call minted, so one call's requests
        # can be read back as a sequence rather than matched by timestamp
        # (GH-117). The window and the role, never the NIP and never the token.
        technical_log().info(
            "KSeF request: metadata page %d, role %s, window %s..%s",
            page_offset,
            subject_role,
            period.date_from.isoformat(),
            period.date_to.isoformat(),
        )
        with translated():
            page = self.authenticated.invoices.query_metadata(
                filters=as_filters(period=period, subject_role=subject_role),
                params=params,
            )
        return MetadataPage(
            invoices=tuple(as_metadata(record) for record in page.invoices),
            has_more=page.has_more,
            truncated=page.is_truncated,
            hwm_date=page.permanent_storage_hwm_date,
            page_offset=page_offset,
        )

    def start_export(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
    ) -> ExportHandle:
        technical_log().info(
            "KSeF request: export for role %s, window %s..%s",
            subject_role,
            period.date_from.isoformat(),
            period.date_to.isoformat(),
        )
        with translated():
            scheduled = self.authenticated.invoices.schedule_export(
                filters=as_filters(period=period, subject_role=subject_role),
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
        technical_log().info("KSeF request: status of export %s", handle.reference)
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
        #
        # The part's ordinal and its export, never the presigned URL: that one
        # is a bearer credential for the package for as long as it lives.
        technical_log().info(
            "KSeF request: part %d of export %s",
            part.ordinal,
            handle.reference,
        )
        try:
            response = self.transport.request(part.method, part.url)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            refusal = PackageLinkExpired if status in EXPIRED_LINK_STATUSES else KsefRefused
            raise refusal(
                f"Storage refused part {part.ordinal} of export {handle.reference} "
                f"with {status}. A package link expires."
            ) from error
        except httpx.HTTPError as error:
            raise KsefUnreachable(f"Could not reach package storage: {error}") from error
        return response.content

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        technical_log().info("KSeF request: invoice %s", ksef_number)
        with translated():
            return self.authenticated.invoices.download_invoice(
                ksef_number=ksef_number.value,
            )


@dataclass(frozen=True)
class Ksef2Port:
    environment: KsefEnvironment

    @contextmanager
    def session(self, *, nip: str, token: Credential) -> Iterator[Ksef2Session]:
        with (
            translated(),
            Client(
                environment=ENVIRONMENTS[self.environment],
                transport_config=TransportConfig(retry=SINGLE_ATTEMPT),
            ) as client,
            httpx.Client(timeout=PART_DOWNLOAD_TIMEOUT) as transport,
        ):
            # The one place the secret is unwrapped, one line before the SDK
            # spends it. Everything upstream carries it as a value that keeps
            # itself out of `repr` (GH-115).
            authenticated = client.authentication.with_token(ksef_token=token.value, nip=nip)
            yield Ksef2Session(authenticated=authenticated, transport=transport)
