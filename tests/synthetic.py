"""Invoice data invented on the spot. Never a real taxpayer, never a real body."""

from datetime import date
from decimal import Decimal

from ksef_mcp.ksef_port import InvoiceMetadata, KsefNumber

SELLER_NIP = "9876543210"

BUYER_NAME = "Moja Firma sp. z o.o."


def synthetic_number(ordinal: int = 1) -> KsefNumber:
    return KsefNumber(f"1234567890-20260901-0100AB12CD{ordinal:02d}-56")


def synthetic_metadata(
    ordinal: int = 1,
    *,
    seller_name: str | None = "Dostawca sp. z o.o.",
) -> InvoiceMetadata:
    return InvoiceMetadata(
        ksef_number=synthetic_number(ordinal),
        seller_invoice_number=f"FV/2026/09/{ordinal:03d}",
        issue_date=date(2026, 9, 1),
        seller_nip=SELLER_NIP,
        seller_name=seller_name,
        buyer_name=BUYER_NAME,
        gross_amount=Decimal("1230.00"),
        net_amount=Decimal("1000.00"),
        vat_amount=Decimal("230.00"),
        currency="PLN",
    )
