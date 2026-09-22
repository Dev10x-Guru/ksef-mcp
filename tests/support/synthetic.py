"""Invoice data invented on the spot. Never a real taxpayer, never a real body.

The package builders below assemble the same shape KSeF hands out — an
encrypted, split ZIP with a `_metadata.json` manifest — out of bodies this
module invents, so a test can drive the whole pipeline without a byte of
anybody's XML reaching the suite (D-011).
"""

import base64
import hashlib
import json
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from ksef2.services.builders.fa3.root import StandardInvoiceBuilder

from ksef_mcp.invoices.package import AES_BLOCK_BITS, METADATA_ENTRY
from ksef_mcp.ksef_port import DocumentType, InvoiceMetadata, KsefNumber
from ksef_mcp.storage.token_store import StoredToken, TokenSource

SELLER_NIP = "9876543210"

BUYER_NAME = "Moja Firma sp. z o.o."


def synthetic_fa3_invoice() -> bytes:
    """An FA(3) document the Ministry's own XSD accepts, built from invented data.

    The PDF generator refuses anything that is not a real invoice, so the stub
    bodies above cannot exercise it. This one goes through `ksef2`'s builder,
    which validates against the packaged schema on the way out — so a test that
    renders it proves the render, not the fixture.
    """
    builder = StandardInvoiceBuilder()
    builder.header(generation_timestamp="2026-09-01T10:00:00Z")
    builder.seller(
        name="Przykładowa Hurtownia sp. z o.o.",
        country_code="PL",
        address_line_1="ul. Testowa 1",
        address_line_2="00-001 Warszawa",
        tax_id=SELLER_NIP,
    )
    builder.buyer(
        name=BUYER_NAME,
        country_code="PL",
        address_line_1="ul. Odbiorcza 7",
        address_line_2="30-001 Kraków",
        tax_id="1234567890",
    )
    body = builder.standard()
    body.currency(value="PLN")
    body.issue_date(value="2026-09-01")
    body.issue_place(value="Warszawa")
    body.invoice_number(value="FV/2026/09/0001")
    rows = body.rows()
    rows.add_row(
        name="Olej napędowy",
        quantity=Decimal("100.000"),
        unit_price_net=Decimal("5.20"),
        vat_rate="23",
        unit_of_measure="l",
    )
    rows.done()
    body.done()
    return builder.to_xml().encode("utf-8")


def synthetic_credential(value: str = "tajny-token") -> StoredToken:
    """A token that belongs to nobody, wrapped the way the port requires.

    A bare string no longer passes, and that is the point (GH-115): the
    `Credential` signature is what keeps `repr` protection in place up to
    the boundary, so a test passing a bare string would bypass exactly the
    invariant it is meant to check.
    """
    return StoredToken(value=value, source=TokenSource.KEYRING)


def synthetic_number(ordinal: int = 1) -> KsefNumber:
    return KsefNumber(f"1234567890-20260901-0100AB12CD{ordinal:02d}-56")


def synthetic_content_hash(ordinal: int) -> str:
    """The 44-character Base64 digest KSeF puts on a metadata row, per ordinal.

    Derived from the ordinal rather than written out, so two synthetic invoices
    never share a digest by accident — a correction matched to the wrong
    original would pass the suite while the linking is broken.
    """
    return base64_digest(f"faktura-{ordinal}".encode())


def synthetic_metadata(
    ordinal: int = 1,
    *,
    seller_name: str | None = "Dostawca sp. z o.o.",
    document_type: DocumentType = DocumentType.VAT,
    corrected_content_hash: str | None = None,
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
        document_type=document_type,
        content_hash=synthetic_content_hash(ordinal),
        corrected_content_hash=corrected_content_hash,
    )


def synthetic_body(ordinal: int = 1) -> bytes:
    return f"<Faktura><Naglowek>syntetyczna {ordinal}</Naglowek></Faktura>".encode()


def synthetic_entry_name(ordinal: int) -> str:
    # Deliberately unlike the KSeF number: the archive takes the file name from
    # the manifest, and a test whose two spellings agree would not notice it
    # taking the wrong one (D-005).
    return f"faktura-{ordinal}.xml"


def synthetic_manifest(*ordinals: int) -> bytes:
    return json.dumps(
        {
            "invoices": [
                {
                    "ksefNumber": str(synthetic_number(ordinal)),
                    "fileName": synthetic_entry_name(ordinal),
                }
                for ordinal in ordinals
            ]
        }
    ).encode("utf-8")


def synthetic_package(*ordinals: int) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for ordinal in ordinals:
            archive.writestr(synthetic_entry_name(ordinal), synthetic_body(ordinal))
        archive.writestr(METADATA_ENTRY, synthetic_manifest(*ordinals))
    return buffer.getvalue()


def aes_encrypted(payload: bytes, *, key: bytes, initialisation_vector: bytes) -> bytes:
    padder = PKCS7(AES_BLOCK_BITS).padder()
    padded = padder.update(payload) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(initialisation_vector)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def base64_digest(payload: bytes) -> str:
    return base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
