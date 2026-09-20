"""Stand-ins for the `ksef2` models, field-for-field.

A double that drifts from the SDK keeps the suite green while the real call
raises `AttributeError` on the user's machine, so `test_port_adapter` asserts
that every annotation here still exists on the model it stands in for.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Self

from ksef2.core.exceptions import KSeFException, KSeFValidationError
from ksef2.domain.models.invoices import InvoiceType

from tests.support.synthetic import BUYER_NAME, SELLER_NIP

HWM = datetime(2026, 9, 10, tzinfo=UTC)


@dataclass
class FakeSeller:
    nip: str
    name: str | None


@dataclass
class FakeBuyer:
    name: str | None


@dataclass
class FakeMetadata:
    ksef_number: str
    invoice_number: str
    issue_date: date
    seller: FakeSeller
    buyer: FakeBuyer
    net_amount: float
    gross_amount: float
    vat_amount: float
    currency: str
    # Wartość SDK-owa, nie drutowa: `ksef2` tłumaczy `Kor` na `kor`, zanim
    # rekord dotrze do adaptera, więc atrapa mówiąca `Kor` sprawdzałaby napis,
    # którego port nigdy nie zobaczy.
    invoice_type: InvoiceType = "vat"


@dataclass
class FakeMetadataPage:
    invoices: list[FakeMetadata]
    has_more: bool
    is_truncated: bool
    permanent_storage_hwm_date: datetime | None


@dataclass
class FakeRateValues:
    per_second: int
    per_minute: int
    per_hour: int


@dataclass
class FakeApiRateLimits:
    invoice_metadata: FakeRateValues
    invoice_export: FakeRateValues
    invoice_export_status: FakeRateValues
    invoice_download: FakeRateValues


@dataclass
class FakeSessionLimits:
    max_invoice_size_mb: int
    max_invoice_with_attachment_size_mb: int
    max_invoices: int


@dataclass
class FakeContextLimits:
    online_session: FakeSessionLimits


@dataclass
class FakeExportHandle:
    reference_number: str
    aes_key: bytes
    iv: bytes


@dataclass
class FakePackagePart:
    ordinal_number: int
    part_name: str
    method: str
    url: str
    part_size: int
    part_hash: str
    encrypted_part_size: int
    encrypted_part_hash: str


@dataclass
class FakeInvoicePackage:
    invoice_count: int
    parts: list[FakePackagePart]
    is_truncated: bool
    last_permanent_storage_date: datetime | None
    permanent_storage_hwm_date: datetime | None


@dataclass
class FakeExportStatusInfo:
    code: int
    description: str


@dataclass
class FakeExportStatusResponse:
    status: FakeExportStatusInfo
    package: FakeInvoicePackage | None
    # Defaulted because most scripts here are about the package, and because
    # `None` is what KSeF sends while an export is still being built. The
    # adapter reads it to tell a closed empty window from one still building
    # (GH-190), so a double without the field would let that mapping rot.
    completed_date: datetime | None = None


def sdk_metadata(
    ordinal: int = 1,
    *,
    seller_name: str | None = "Dostawca sp. z o.o.",
    invoice_type: InvoiceType = "vat",
) -> FakeMetadata:
    return FakeMetadata(
        ksef_number=f"1234567890-20260901-0100AB12CD{ordinal:02d}-56",
        invoice_number=f"FV/2026/09/{ordinal:03d}",
        issue_date=date(2026, 9, 1),
        seller=FakeSeller(nip=SELLER_NIP, name=seller_name),
        buyer=FakeBuyer(name=BUYER_NAME),
        net_amount=1000.0,
        gross_amount=1230.0,
        vat_amount=230.0,
        currency="PLN",
        invoice_type=invoice_type,
    )


def sdk_part(ordinal: int = 1) -> FakePackagePart:
    return FakePackagePart(
        ordinal_number=ordinal,
        part_name=f"package_part_{ordinal}.zip.aes",
        method="GET",
        url=f"https://storage.example/part/{ordinal}",
        part_size=1024,
        part_hash="c3BsaXQ=",
        encrypted_part_size=1040,
        encrypted_part_hash="ZW5jcnlwdGVk",
    )


def sdk_rate_limits() -> FakeApiRateLimits:
    return FakeApiRateLimits(
        invoice_metadata=FakeRateValues(per_second=8, per_minute=16, per_hour=20),
        invoice_export=FakeRateValues(per_second=2, per_minute=4, per_hour=20),
        invoice_export_status=FakeRateValues(per_second=8, per_minute=16, per_hour=200),
        invoice_download=FakeRateValues(per_second=4, per_minute=16, per_hour=64),
    )


def sdk_context_limits() -> FakeContextLimits:
    return FakeContextLimits(
        online_session=FakeSessionLimits(
            max_invoice_size_mb=1,
            max_invoice_with_attachment_size_mb=3,
            max_invoices=10_000,
        )
    )


class FakeLimitsClient:
    def get_api_rate_limits(self) -> FakeApiRateLimits:
        return sdk_rate_limits()

    def get_context_limits(self) -> FakeContextLimits:
        return sdk_context_limits()


# Production answers 200 but omits `collectiveIdentifier`, which the SDK model
# requires, so the SDK raises rather than returning a partial object. The two
# reads fail independently — hence the flags (GH-76).
@dataclass
class FakeUnparsableLimitsClient:
    rates_unparsable: bool = True
    ceilings_unparsable: bool = True

    def get_api_rate_limits(self) -> FakeApiRateLimits:
        if self.rates_unparsable:
            raise KSeFValidationError("Invalid response payload")
        return sdk_rate_limits()

    def get_context_limits(self) -> FakeContextLimits:
        if self.ceilings_unparsable:
            raise KSeFValidationError("Invalid response payload")
        return sdk_context_limits()


class RefusingLimitsClient:
    """KSeF answered with an error, not with a payload we failed to read."""

    def get_api_rate_limits(self) -> FakeApiRateLimits:
        raise KSeFException("Context is not authorised for this operation")

    def get_context_limits(self) -> FakeContextLimits:
        raise KSeFException("Context is not authorised for this operation")


@dataclass
class FakeInvoicesService:
    page: list[FakeMetadata]
    status: FakeExportStatusResponse
    error: Exception | None = None
    # Pages beyond the first, keyed by their zero-based number. Without them
    # this double answered `has_more=False` to every question, so the adapter's
    # translation of pagination was the one path no test ever walked (GH-179).
    further: dict[int, FakeMetadataPage] = field(default_factory=dict)
    metadata_calls: list[object] = field(default_factory=list)
    export_calls: list[object] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)

    def query_metadata(self, *, filters: object, params: object) -> FakeMetadataPage:
        self._fail_if_planned()
        self.metadata_calls.append((filters, params))
        planned = self.further.get(getattr(params, "page_offset", 0))
        if planned is not None:
            return planned
        return FakeMetadataPage(
            invoices=list(self.page),
            # A first page says there is more exactly when a continuation was
            # scripted, so a test states the shape of the window in one place.
            has_more=bool(self.further),
            is_truncated=False,
            permanent_storage_hwm_date=HWM,
        )

    def schedule_export(self, *, filters: object) -> FakeExportHandle:
        self._fail_if_planned()
        self.export_calls.append(filters)
        return FakeExportHandle(reference_number="EXP-1", aes_key=b"k" * 32, iv=b"i" * 16)

    def get_export_status(self, *, reference_number: str) -> FakeExportStatusResponse:
        self._fail_if_planned()
        return self.status

    def download_invoice(self, *, ksef_number: str) -> bytes:
        self._fail_if_planned()
        self.downloaded.append(ksef_number)
        return b"<Faktura/>"

    def _fail_if_planned(self) -> None:
        if self.error is not None:
            raise self.error


@dataclass
class FakeAuthenticated:
    invoices: FakeInvoicesService
    limits: FakeLimitsClient


@dataclass
class FakeAuthentication:
    authenticated: FakeAuthenticated
    error: Exception | None
    credentials: list[tuple[str, str]] = field(default_factory=list)

    def with_token(self, *, ksef_token: str, nip: str) -> FakeAuthenticated:
        self.credentials.append((nip, ksef_token))
        if self.error is not None:
            raise self.error
        return self.authenticated


@dataclass
class FakeSdkClient:
    environment: object
    transport_config: object
    authentication: FakeAuthentication
    closed: bool = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True
