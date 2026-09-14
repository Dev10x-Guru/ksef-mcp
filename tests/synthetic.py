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

from ksef_mcp.ksef_port import InvoiceMetadata, KsefNumber
from ksef_mcp.package import AES_BLOCK_BITS, METADATA_ENTRY

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
