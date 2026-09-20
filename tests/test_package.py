"""Reading a finished export, and the key that does not outlive it.

Every package here is built in the test: a synthetic ZIP, encrypted with a key
this file invents, served by a scripted session. Nothing reaches KSeF and no
invoice body in the suite belongs to a real taxpayer.

The assertions come in two families. One is about bytes — parts joined in
ordinal order, hashes checked on both sides of the cipher, a ZIP that unpacks.
The other is about the key: it disappears exactly when the package is archived,
and it survives every failure before that, because a record without a key is a
window nobody can fetch again (D-033).
"""

import base64
import hashlib
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from ksef_mcp.ksef_port import (
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    KsefEnvironment,
    KsefLimits,
    KsefNumber,
    KsefUnreachable,
    MetadataPage,
    Period,
    SubjectRole,
)
from ksef_mcp.package import (
    AES_BLOCK_BITS,
    METADATA_ENTRY,
    ArchivedExport,
    ExportPackage,
    PackageRetriever,
    PackageUnreadable,
    digest_matches,
    escapes_package,
    unpacked,
)
from ksef_mcp.storage.sync_store import ExportKeyDiscarded, PendingExport, SyncState, SyncStore
from tests.support.synthetic import synthetic_number

NIP = "1234567890"

KEY = b"k" * 32

IV = b"i" * 16

STARTED = datetime(2026, 9, 11, 8, 30, tzinfo=UTC)

INVOICE = b"<Faktura><Naglowek>syntetyczna</Naglowek></Faktura>"

METADATA = json.dumps({"invoices": [str(synthetic_number())]}).encode("utf-8")


def encrypted(payload: bytes) -> bytes:
    padder = PKCS7(AES_BLOCK_BITS).padder()
    padded = padder.update(payload) + padder.finalize()
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def digest(payload: bytes) -> str:
    return base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")


def a_zip(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def halves(payload: bytes) -> tuple[bytes, bytes]:
    middle = len(payload) // 2
    return payload[:middle], payload[middle:]


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    plain: bytes

    @property
    def cipher(self) -> bytes:
        return encrypted(self.plain)

    @property
    def part(self) -> ExportPart:
        return ExportPart(
            ordinal=self.ordinal,
            name=f"package_part_{self.ordinal}.zip.aes",
            method="GET",
            url=f"https://storage.example/part/{self.ordinal}",
            size_bytes=len(self.plain),
            content_hash=digest(self.plain),
            encrypted_size_bytes=len(self.cipher),
            encrypted_content_hash=digest(self.cipher),
        )


@dataclass
class ScriptedSession:
    payloads: dict[str, bytes]
    failure: Exception | None = None
    fetched: list[int] = field(default_factory=list)

    def read_limits(self) -> KsefLimits:
        raise AssertionError("Odczyt paczki nie pyta o limity.")

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        raise AssertionError("Odczyt paczki nie odpytuje metadanych.")

    def start_export(self, *, period: Period, subject_role: SubjectRole) -> ExportHandle:
        raise AssertionError("Odczyt paczki nie zamawia eksportu.")

    def check_export(self, *, handle: ExportHandle) -> object:
        raise AssertionError("Odczyt paczki nie odpytuje o status.")

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        self.fetched.append(part.ordinal)
        if self.failure is not None:
            raise self.failure
        return self.payloads[part.url]

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        raise AssertionError("Odczyt paczki nie pobiera pojedynczych faktur.")


@dataclass
class Archivist:
    """Stands in for #38: takes the decrypted package, records that it was called."""

    seen: list[ExportPackage] = field(default_factory=list)
    failure: Exception | None = None

    def __call__(self, package: ExportPackage) -> None:
        self.seen.append(package)
        if self.failure is not None:
            raise self.failure


@pytest.fixture
def package_bytes() -> bytes:
    return a_zip({f"{synthetic_number()}.xml": INVOICE, METADATA_ENTRY: METADATA})


@pytest.fixture
def chunks(package_bytes: bytes) -> tuple[Chunk, Chunk]:
    first, second = halves(package_bytes)
    return Chunk(ordinal=1, plain=first), Chunk(ordinal=2, plain=second)


@pytest.fixture
def session(chunks: tuple[Chunk, Chunk]) -> ScriptedSession:
    return ScriptedSession(payloads={chunk.part.url: chunk.cipher for chunk in chunks})


@pytest.fixture
def store(tmp_path: Path) -> SyncStore:
    return SyncStore(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)


def a_pending(*, parts: tuple[ExportPart, ...]) -> PendingExport:
    return PendingExport(
        reference="EXP-1",
        subject_role=SubjectRole.BUYER,
        started_at=STARTED,
        encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
        state=ExportState.READY,
        parts=parts,
        invoice_count=1,
    )


@pytest.fixture
def ready(chunks: tuple[Chunk, Chunk]) -> PendingExport:
    # Reversed on purpose: the record's order is not the stream's order.
    return a_pending(parts=(chunks[1].part, chunks[0].part))


@pytest.fixture
def retriever(session: ScriptedSession, store: SyncStore) -> PackageRetriever:
    return PackageRetriever(session=session, store=store)


@pytest.fixture
def recorded(store: SyncStore, ready: PendingExport) -> SyncStore:
    store.save(SyncState().with_pending(ready))
    return store


@pytest.fixture
def archivist() -> Archivist:
    return Archivist()


@pytest.fixture
def archived(
    retriever: PackageRetriever,
    recorded: SyncStore,
    ready: PendingExport,
    archivist: Archivist,
) -> ArchivedExport:
    return retriever.archive(export=ready, archivist=archivist)


def refusal(
    retriever: PackageRetriever,
    *,
    export: PendingExport,
    archivist: Callable[[ExportPackage], None],
) -> type[BaseException]:
    with pytest.raises(BaseException) as raised:
        retriever.archive(export=export, archivist=archivist)
    return type(raised.value)


def test_a_package_split_across_parts_is_read_as_one_zip(
    retriever: PackageRetriever, ready: PendingExport
) -> None:
    assert [document.name for document in retriever.collect(export=ready).documents] == [
        f"{synthetic_number()}.xml"
    ]


def test_the_invoice_survives_the_round_trip_byte_for_byte(
    retriever: PackageRetriever, ready: PendingExport
) -> None:
    assert retriever.collect(export=ready).documents[0].content == INVOICE


def test_parts_are_fetched_in_ordinal_order_whatever_the_record_says(
    retriever: PackageRetriever, ready: PendingExport, session: ScriptedSession
) -> None:
    retriever.collect(export=ready)

    assert session.fetched == [1, 2]


def test_the_deduplication_input_is_handed_on_separately(
    retriever: PackageRetriever, ready: PendingExport
) -> None:
    # `_metadata.json` is the input to deduplication by KSeF number (D-031 §6),
    # so #38 has to see it as something other than one more invoice.
    assert retriever.collect(export=ready).metadata == METADATA


def test_a_package_without_the_metadata_entry_says_so_rather_than_inventing_one() -> None:
    assert unpacked(a_zip({"one.xml": INVOICE}), reference="EXP-1").metadata is None


def test_directory_entries_are_not_mistaken_for_documents() -> None:
    assert unpacked(
        a_zip({"faktury/": b"", "faktury/one.xml": INVOICE}), reference="EXP-1"
    ).documents == (unpacked(a_zip({"faktury/one.xml": INVOICE}), reference="EXP-1").documents)


@pytest.mark.parametrize(
    "name",
    ["../poza.xml", "/etc/passwd", "faktury/../../poza.xml"],
    ids=["parent", "absolute", "nested-parent"],
)
def test_an_entry_pointing_outside_the_package_is_refused(name: str) -> None:
    with pytest.raises(PackageUnreadable, match="outside the package"):
        unpacked(a_zip({name: INVOICE}), reference="EXP-1")


@pytest.mark.parametrize(
    "name",
    ["faktury/one.xml", "one.xml"],
    ids=["nested", "flat"],
)
def test_an_ordinary_entry_is_not_mistaken_for_an_escape(name: str) -> None:
    assert escapes_package(name) is False


def test_a_hash_spelled_in_hex_is_accepted_like_one_spelled_in_base64() -> None:
    assert digest_matches(INVOICE, declared=hashlib.sha256(INVOICE).hexdigest()) is True


def test_a_hash_that_matches_nothing_is_rejected() -> None:
    assert digest_matches(INVOICE, declared="nie-ten-skrot") is False


def test_bytes_that_do_not_decrypt_into_a_zip_are_refused(
    store: SyncStore, chunks: tuple[Chunk, Chunk]
) -> None:
    rubbish = Chunk(ordinal=1, plain=b"to nie jest ZIP")
    retriever = PackageRetriever(
        session=ScriptedSession(payloads={rubbish.part.url: rubbish.cipher}),
        store=store,
    )

    with pytest.raises(PackageUnreadable, match="not a ZIP"):
        retriever.collect(export=a_pending(parts=(rubbish.part,)))


def test_a_part_whose_length_differs_from_the_declaration_is_refused(
    retriever: PackageRetriever, chunks: tuple[Chunk, Chunk]
) -> None:
    declared = chunks[0].part
    lying = ExportPart(
        ordinal=declared.ordinal,
        name=declared.name,
        method=declared.method,
        url=declared.url,
        size_bytes=declared.size_bytes,
        content_hash=declared.content_hash,
        encrypted_size_bytes=declared.encrypted_size_bytes + 1,
        encrypted_content_hash=declared.encrypted_content_hash,
    )

    with pytest.raises(PackageUnreadable, match="as downloaded"):
        retriever.collect(export=a_pending(parts=(lying,)))


def test_a_part_whose_hash_differs_from_the_declaration_is_refused(
    retriever: PackageRetriever, chunks: tuple[Chunk, Chunk]
) -> None:
    declared = chunks[0].part
    lying = ExportPart(
        ordinal=declared.ordinal,
        name=declared.name,
        method=declared.method,
        url=declared.url,
        size_bytes=declared.size_bytes,
        content_hash=declared.content_hash,
        encrypted_size_bytes=declared.encrypted_size_bytes,
        encrypted_content_hash=digest(b"co innego"),
    )

    with pytest.raises(PackageUnreadable, match="does not match the"):
        retriever.collect(export=a_pending(parts=(lying,)))


def test_a_part_that_decrypts_to_something_other_than_declared_is_refused(
    store: SyncStore, chunks: tuple[Chunk, Chunk]
) -> None:
    honest = chunks[0]
    lying = ExportPart(
        ordinal=honest.part.ordinal,
        name=honest.part.name,
        method=honest.part.method,
        url=honest.part.url,
        size_bytes=honest.part.size_bytes,
        content_hash=digest(b"co innego"),
        encrypted_size_bytes=honest.part.encrypted_size_bytes,
        encrypted_content_hash=honest.part.encrypted_content_hash,
    )
    retriever = PackageRetriever(
        session=ScriptedSession(payloads={honest.part.url: honest.cipher}),
        store=store,
    )

    with pytest.raises(PackageUnreadable, match="after decryption"):
        retriever.collect(export=a_pending(parts=(lying,)))


def test_a_key_that_does_not_belong_to_the_package_is_refused(
    store: SyncStore, chunks: tuple[Chunk, Chunk]
) -> None:
    honest = chunks[0]
    retriever = PackageRetriever(
        session=ScriptedSession(payloads={honest.part.url: honest.cipher}),
        store=store,
    )
    wrong = PendingExport(
        reference="EXP-1",
        subject_role=SubjectRole.BUYER,
        started_at=STARTED,
        encryption=ExportEncryption(key=b"x" * 32, initialisation_vector=IV),
        state=ExportState.READY,
        parts=(honest.part,),
    )

    with pytest.raises(PackageUnreadable, match="decryption of a part"):
        retriever.collect(export=wrong)


def test_the_failure_never_repeats_the_package_content(
    store: SyncStore, chunks: tuple[Chunk, Chunk]
) -> None:
    # An exception message is logged, and FA(2)/FA(3) XML carries the
    # counterparty's personal data (D-011).
    rubbish = Chunk(ordinal=1, plain=INVOICE)
    retriever = PackageRetriever(
        session=ScriptedSession(payloads={rubbish.part.url: rubbish.cipher}),
        store=store,
    )

    with pytest.raises(PackageUnreadable) as raised:
        retriever.collect(export=a_pending(parts=(rubbish.part,)))

    assert "Faktura" not in str(raised.value)


def test_an_export_with_no_parts_on_record_is_refused(retriever: PackageRetriever) -> None:
    with pytest.raises(PackageUnreadable, match="no parts on record"):
        retriever.collect(export=a_pending(parts=()))


def test_an_export_whose_key_is_already_gone_cannot_be_collected(
    retriever: PackageRetriever, chunks: tuple[Chunk, Chunk]
) -> None:
    spent = PendingExport(
        reference="EXP-1",
        subject_role=SubjectRole.BUYER,
        started_at=STARTED,
        encryption=None,
        state=ExportState.FAILED,
        parts=(chunks[0].part,),
    )

    with pytest.raises(ExportKeyDiscarded, match="carries no key"):
        retriever.collect(export=spent)


def test_nothing_is_downloaded_for_an_export_whose_key_is_gone(
    retriever: PackageRetriever, chunks: tuple[Chunk, Chunk], session: ScriptedSession
) -> None:
    spent = PendingExport(
        reference="EXP-1",
        subject_role=SubjectRole.BUYER,
        started_at=STARTED,
        encryption=None,
        state=ExportState.FAILED,
        parts=(chunks[0].part,),
    )
    refusal(retriever, export=spent, archivist=Archivist())

    assert session.fetched == []


def test_the_archivist_is_handed_the_decrypted_package(
    archived: ArchivedExport, archivist: Archivist
) -> None:
    assert archivist.seen[0].documents[0].content == INVOICE


def test_archiving_reports_what_landed(archived: ArchivedExport) -> None:
    assert (archived.reference, archived.document_count) == ("EXP-1", 1)


def test_the_record_is_gone_once_the_package_is_archived(
    archived: ArchivedExport, recorded: SyncStore
) -> None:
    assert recorded.load().pending == ()


def test_the_key_is_not_left_anywhere_on_disk(
    archived: ArchivedExport, recorded: SyncStore
) -> None:
    # The whole invariant of D-033 in one assertion: after archiving there is
    # no spelling of the key left in the record the next run reads.
    assert base64.b64encode(KEY).decode("ascii") not in recorded.path.read_text(encoding="utf-8")


def test_archiving_reports_the_record_it_rewrote(
    archived: ArchivedExport, recorded: SyncStore
) -> None:
    assert archived.state_path == str(recorded.path)


def test_a_failed_part_download_leaves_the_key_where_it_was(
    retriever: PackageRetriever,
    recorded: SyncStore,
    ready: PendingExport,
    session: ScriptedSession,
) -> None:
    session.failure = KsefUnreachable("Storage nie odpowiada.")
    refusal(retriever, export=ready, archivist=Archivist())

    assert recorded.load().pending[0].encryption == ExportEncryption(
        key=KEY, initialisation_vector=IV
    )


def test_a_failed_part_download_leaves_the_export_queued(
    retriever: PackageRetriever,
    recorded: SyncStore,
    ready: PendingExport,
    session: ScriptedSession,
) -> None:
    # The acceptance criterion of #37: a part that did not arrive must not let
    # anything treat the window as fetched.
    session.failure = KsefUnreachable("Storage nie odpowiada.")
    refusal(retriever, export=ready, archivist=Archivist())

    assert recorded.load().pending_for(SubjectRole.BUYER) == ready


def test_an_archivist_that_fails_keeps_the_key_and_the_record(
    retriever: PackageRetriever, recorded: SyncStore, ready: PendingExport
) -> None:
    refusal(retriever, export=ready, archivist=Archivist(failure=OSError("Dysk pełny.")))

    assert recorded.load().pending_for(SubjectRole.BUYER) == ready


def test_the_archivist_failure_reaches_the_caller_unchanged(
    retriever: PackageRetriever, recorded: SyncStore, ready: PendingExport
) -> None:
    assert (
        refusal(retriever, export=ready, archivist=Archivist(failure=OSError("Dysk pełny.")))
        is OSError
    )
