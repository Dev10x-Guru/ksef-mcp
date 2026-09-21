"""Turning a finished export into invoice bytes, and losing the key on the way.

KSeF hands out a package as an encrypted ZIP split into parts, each behind its
own presigned link. Reading it means four steps in one order: fetch the part,
decrypt it with the AES-256 key minted at initialisation, join the parts into a
stream, unpack the ZIP (D-031 §7).

The key is the point of this module as much as the invoices are. It lives at the
pending-export record, never in the keyring, and it stops existing as part of
archiving the package rather than in a cleanup pass somebody has to remember to
run (D-033). That is why `archive` takes the archivist as an argument: the
record — and the key inside it — is dropped inside the same operation that put
the invoices somewhere durable, and a failure anywhere before that leaves both
the key and the parts on disk for the next run.
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Final

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.protocol import KsefSession
from ksef_mcp.ksef_port.types import (
    ExportEncryption,
    ExportHandle,
    ExportPackage,
    ExportPart,
    PackageDocument,
)
from ksef_mcp.storage.sync_store import PendingExport, SyncStore

# Always present since 27.10.2025 and the input to deduplication by KSeF number
# — it does not replace deduplication, it feeds it (D-031 §6).
METADATA_ENTRY: Final[str] = "_metadata.json"

AES_BLOCK_BITS: Final[int] = 128


class PackageUnreadable(KsefMcpError):
    """The bytes that arrived are not the package KSeF described.

    Raised before anything is archived, so the pending record keeps its key and
    the same package can be fetched again. Never carries package content: an
    exception message is logged, and FA(2)/FA(3) XML holds personal data (D-011).
    """


def digest_matches(payload: bytes, *, declared: str) -> bool:
    digest = hashlib.sha256(payload).digest()
    # KSeF prints these hashes base64; the same field turns up hex-spelled
    # elsewhere in the ecosystem. Accepting either spelling costs nothing,
    # while rejecting a sound package over one would burn the export that
    # produced it — twenty an hour, shared between four subject types.
    return declared in (base64.b64encode(digest).decode("ascii"), digest.hex())


def verified(
    payload: bytes,
    *,
    reference: str,
    part: ExportPart,
    stage: str,
    size_bytes: int,
    content_hash: str,
) -> bytes:
    if len(payload) != size_bytes:
        raise PackageUnreadable(
            f"Part {part.ordinal} of export {reference} is {len(payload)} bytes "
            f"{stage}, and KSeF declared {size_bytes}. Refusing to archive a "
            f"package that is not the one described."
        )
    if not digest_matches(payload, declared=content_hash):
        raise PackageUnreadable(
            f"Part {part.ordinal} of export {reference} does not match the "
            f"SHA-256 KSeF declared for it {stage}."
        )
    return payload


def decrypted(payload: bytes, *, reference: str, encryption: ExportEncryption) -> bytes:
    decryptor = Cipher(
        algorithms.AES(encryption.key),
        modes.CBC(encryption.initialisation_vector),
    ).decryptor()
    unpadder = PKCS7(AES_BLOCK_BITS).unpadder()
    try:
        opened = decryptor.update(payload) + decryptor.finalize()
        return unpadder.update(opened) + unpadder.finalize()
    except ValueError as error:
        raise PackageUnreadable(
            f"AES-256-CBC decryption of a part of export {reference} failed. "
            f"The key and IV on record belong to a different export, or the "
            f"downloaded bytes are truncated."
        ) from error


def escapes_package(name: str) -> bool:
    entry = PurePosixPath(name)
    return entry.is_absolute() or ".." in entry.parts


@dataclass(frozen=True)
class ArchivedExport:
    """What is safe to say out loud once the package is stored and the key is gone."""

    reference: str
    document_count: int
    state_path: str


def unpacked(stream: bytes, *, reference: str) -> ExportPackage:
    try:
        with zipfile.ZipFile(BytesIO(stream)) as archive:
            entries = {
                entry.filename: archive.read(entry)
                for entry in archive.infolist()
                if not entry.is_dir()
            }
    except zipfile.BadZipFile as error:
        raise PackageUnreadable(
            f"Export {reference} decrypted into something that is not a ZIP."
        ) from error
    escaping = sorted(name for name in entries if escapes_package(name))
    if escaping:
        # The archive writes these names to disk (#38). An entry reaching
        # outside the package would be a path traversal handed to us by
        # whoever controls the presigned storage, so it fails here.
        raise PackageUnreadable(
            f"Export {reference} carries an entry pointing outside the package: "
            f"{escaping[0]!r}. Refusing to unpack it."
        )
    return ExportPackage(
        reference=reference,
        documents=tuple(
            PackageDocument(name=name, content=content)
            for name, content in sorted(entries.items())
            if name != METADATA_ENTRY
        ),
        metadata=entries.get(METADATA_ENTRY),
    )


@dataclass(frozen=True)
class PackageRetriever:
    """Reads one ready export end to end, and forgets its key when it lands."""

    session: KsefSession
    store: SyncStore

    def collect(self, *, export: PendingExport) -> ExportPackage:
        if not export.parts:
            raise PackageUnreadable(
                f"Export {export.reference} has no parts on record. Only a "
                f"package KSeF reported as ready can be collected."
            )
        # Read once and up front: a record whose key was already discarded says
        # so here, before a single part is downloaded.
        handle = export.handle
        # Ordinal order, not the order the record happens to carry: the parts
        # are chunks of one ZIP, and a stream assembled out of order unpacks
        # into nothing at all.
        stream = b"".join(
            self._opened(handle=handle, part=part)
            for part in sorted(export.parts, key=lambda part: part.ordinal)
        )
        return unpacked(stream, reference=export.reference)

    def archive(
        self,
        *,
        export: PendingExport,
        archivist: Callable[[ExportPackage], None],
    ) -> ArchivedExport:
        """Collect the package, hand it to `archivist`, then drop key and record.

        Nothing is forgotten until the archivist returns. A failed part, a
        corrupt package or an archivist that raises all leave the pending record
        intact, so the window is not treated as fetched and the next run
        retries it without spending another export (D-033, ADR-103 §3).
        """
        package = self.collect(export=export)
        archivist(package)
        # One exclusive cycle, not a read followed by an unrelated write: a
        # second writer slipping between the two used to resurrect the record
        # this call exists to drop, and with it the key (ADR-107 §2).
        self.store.updating(lambda state: state.without_export(reference=export.reference))
        return ArchivedExport(
            reference=export.reference,
            document_count=len(package.documents),
            state_path=str(self.store.path),
        )

    def _opened(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        encrypted = verified(
            self.session.fetch_part(handle=handle, part=part),
            reference=handle.reference,
            part=part,
            stage="as downloaded",
            size_bytes=part.encrypted_size_bytes,
            content_hash=part.encrypted_content_hash,
        )
        return verified(
            decrypted(encrypted, reference=handle.reference, encryption=handle.encryption),
            reference=handle.reference,
            part=part,
            stage="after decryption",
            size_bytes=part.size_bytes,
            content_hash=part.content_hash,
        )
