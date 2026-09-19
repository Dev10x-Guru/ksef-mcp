"""Where invoices land, and why the file name is the whole invariant.

`WpisArchiwum` is the only aggregate, its identity is the KSeF number, and the
invariant — the same invoice is not stored twice — has no transaction to live
in. On a filesystem `rename(2)` inside one directory is the only atomic
primitive there is, so the invariant is enforced by writing `<KSeF number>.xml`
through a staging file and renaming it into place (D-006). Two runs carrying
the same invoice converge on one file because they derive the same name, not
because a set in memory told them to.

The name comes from `_metadata.json`, never from the entry name inside the
package and never from reading the invoice. A script that guessed from file
names reported ten and then seven missing invoices where four were missing;
the manifest carries the KSeF numbers, so it is the input to deduplication
rather than a substitute for it (D-005, #38).

Which document a KSeF number belongs to comes from the digest the manifest
states, not from a name it does not (GH-87). `_metadata.json` holds
`InvoiceMetadata` entries — the same model the metadata query returns — and
that model has no file-name field at all, so the first production package
refused itself. Pairing on `invoiceHash` is what the file name was reaching
for: derived from the bytes, it cannot point at the wrong document even when
the package names its entries however it likes.

Each subject gets its own subdirectory, per environment, because a shared one
is the main vector for mixing an accounting office's clients (D-032, D-034).
The deduplication index is a separate file from the invoices it describes, and
that separation is what lets the retention command delete invoice bodies
without the next synchronisation fetching every one of them again (D-034).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.errors import InvalidKsefIdentifier
from ksef_mcp.ksef_port.types import KsefNumber
from ksef_mcp.package import ExportPackage
from ksef_mcp.paths import SubjectScope
from ksef_mcp.storage import exclusive_write, require_schema, written_atomically

INVOICE_DIRECTORY: Final[str] = "invoices"

INDEX_FILE: Final[str] = "deduplication.json"

INVOICE_SUFFIX: Final[str] = ".xml"

SCHEMA_VERSION: Final[int] = 1

ARCHIVE_DIRECTORY_MODE: Final[int] = 0o700

# Invoice bodies carry a contractor's personal data (D-011), so the files are
# created with their final mode rather than written and then tightened.
ARCHIVE_FILE_MODE: Final[int] = 0o600

# The manifest is written by KSeF, and the spellings below are the ones seen in
# the wild. Reading several costs nothing; guessing the KSeF number from the
# package's own file names when none of them is present would cost correctness.
INVOICE_LIST_KEYS: Final[tuple[str, ...]] = ("invoices", "faktury")

KSEF_NUMBER_KEYS: Final[tuple[str, ...]] = ("ksefNumber", "ksef_number", "numerKSeF")

# What actually pairs a manifest line with a document (GH-87). The manifest is
# `{"invoices": [InvoiceMetadata]}` — the same model `POST
# /invoices/query/metadata` returns — and `InvoiceMetadata` carries no file
# name under any spelling. Every first production run therefore refused its own
# package: the key below was not misspelled, it was absent by design.
#
# `invoiceHash` is mandatory in that model and is the SHA-256 of the invoice in
# base64. Pairing on it is what the file name was reaching for and could not
# reach: it is derived from the bytes, so it answers the objection the file
# name was there to answer — "pairing them by position would archive one
# invoice under another's number" — better than a name ever did.
INVOICE_HASH_KEYS: Final[tuple[str, ...]] = ("invoiceHash", "invoice_hash", "skrotFaktury")

# Kept because a manifest that does name a file is still honoured, and because
# nothing in MF's documentation forbids one appearing later. It is no longer
# the only way in.
FILE_NAME_KEYS: Final[tuple[str, ...]] = ("fileName", "file_name", "nazwaPliku")


class ArchiveMetadataUnusable(KsefMcpError):
    """`_metadata.json` does not say which invoice is which.

    Raised before a single file is written, so the pending export keeps its key
    and the same package can be archived again once the manifest is understood.
    Never carries invoice content: the message is logged, and FA(2)/FA(3) XML
    holds personal data (D-011).
    """


class ArchiveIndexUnreadable(KsefMcpError):
    """The deduplication index was written by something this build cannot read."""


class ArchiveNotPerformed(KsefMcpError):
    """Asked what an archivist stored before it stored anything."""


class IndexEntryAlreadyHeld(KsefMcpError):
    """A KSeF number was offered to the index twice.

    The invoice file has always refused its own duplicate structurally — the
    name is the identity, and `_written` reports an existing target as already
    held rather than replacing it. The index entry had no such guard: it was
    checked for uniqueness and never made to keep it, so two passes starting
    from one snapshot silently dropped the earlier pass's entries (GH-103).
    """


@dataclass(frozen=True)
class InvoiceIdentity:
    """One line of the manifest: which entry of the package is which invoice.

    Either way of pointing at the entry is enough. A line carrying both is
    settled by the digest alone: if it matches, the name adds nothing, and if
    it does not, the two disagree and the name is the half more likely to be
    wrong.
    """

    ksef_number: KsefNumber
    file_name: str | None
    content_hash: str | None


@dataclass(frozen=True)
class IndexEntry:
    """A KSeF number this subject has held, and the digest of what was held.

    Kept apart from the invoice it describes on purpose. The entry outlives a
    deleted body, so retention frees the disk without costing idempotence
    (D-005, D-034).
    """

    ksef_number: str
    content_hash: str
    archived_at: datetime


@dataclass(frozen=True)
class DeduplicationIndex:
    entries: tuple[IndexEntry, ...] = ()

    @property
    def known(self) -> frozenset[str]:
        return frozenset(entry.ksef_number for entry in self.entries)

    def with_entry(self, entry: IndexEntry) -> DeduplicationIndex:
        """Add a number the index does not hold, and refuse one it does.

        This is the structural half of the guarantee; `InvoiceArchive.store`
        supplies the other by doing its whole read-change-write under the
        subject's lock (ADR-107 §2). Neither alone is enough: the lock without
        this refusal would still let a caller add a number twice, and this
        refusal without the lock would still start from a stale snapshot.
        """
        if entry.ksef_number in self.known:
            raise IndexEntryAlreadyHeld(
                f"The deduplication index already holds {entry.ksef_number}. A "
                f"second entry for one KSeF number would make the index answer "
                f"two different things about the same invoice."
            )
        return replace(self, entries=(*self.entries, entry))


@dataclass(frozen=True)
class ArchiveReport:
    """What is safe to say out loud once the package is on disk — paths, never bodies."""

    directory: str
    index_path: str
    archived: tuple[str, ...]
    already_held: tuple[str, ...]


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def digest_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def stated_digest_of(content: bytes) -> str:
    """The same digest in the spelling the manifest uses: base64, not hex.

    Separate from `digest_of` on purpose. That one is ours and lives in the
    deduplication index, where the encoding is nobody's business but ours;
    this one has to match bytes MF wrote, so its encoding is theirs.
    """
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def _stated(entry: Mapping[str, object], *, keys: tuple[str, ...]) -> object | None:
    return next((entry[key] for key in keys if key in entry), None)


def _listed(document: object) -> list[object]:
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        invoices = _stated(document, keys=INVOICE_LIST_KEYS)
        if isinstance(invoices, list):
            return invoices
    raise ArchiveMetadataUnusable(
        f"_metadata.json holds no list of invoices under any of "
        f"{list(INVOICE_LIST_KEYS)}, so it names no KSeF number. Refusing to "
        f"deduplicate on the package's own file names instead (D-005)."
    )


def _identity(entry: object) -> InvoiceIdentity:
    if not isinstance(entry, dict):
        raise ArchiveMetadataUnusable(
            f"An entry of _metadata.json is {type(entry).__name__}, not an "
            f"object, so it pairs no KSeF number with a file in the package."
        )
    number = _stated(entry, keys=KSEF_NUMBER_KEYS)
    if number is None:
        raise ArchiveMetadataUnusable(
            f"An entry of _metadata.json carries no KSeF number under any of "
            f"{list(KSEF_NUMBER_KEYS)}. That number is the identity of the "
            f"archived invoice and nothing else can stand in for it."
        )
    file_name = _stated(entry, keys=FILE_NAME_KEYS)
    content_hash = _stated(entry, keys=INVOICE_HASH_KEYS)
    if file_name is None and content_hash is None:
        raise ArchiveMetadataUnusable(
            f"_metadata.json states KSeF number {number!r} but points at no "
            f"document: no digest under any of {list(INVOICE_HASH_KEYS)} and no "
            f"file name under any of {list(FILE_NAME_KEYS)}. The entry does "
            f"carry {sorted(entry)}. Pairing by position would archive one "
            f"invoice under another's number."
        )
    try:
        return InvoiceIdentity(
            ksef_number=KsefNumber(str(number)),
            file_name=None if file_name is None else str(file_name),
            # Trimmed because the comparison is exact and the digest is
            # somebody else's serialisation: a stray space would refuse a
            # package that is entirely correct.
            content_hash=None if content_hash is None else str(content_hash).strip(),
        )
    except InvalidKsefIdentifier as rejection:
        raise ArchiveMetadataUnusable(
            f"_metadata.json offers {number!r} as a KSeF number: {rejection}"
        ) from rejection


def _by_content(bodies: Mapping[str, bytes]) -> dict[str, str]:
    """Digest to entry name, dropping any digest two entries share.

    A shared digest means two entries hold the same bytes, and then the digest
    names neither of them. Dropping it turns that into the same refusal an
    absent entry gets, rather than a coin toss between two KSeF numbers.
    """
    found: dict[str, list[str]] = {}
    for name, content in bodies.items():
        found.setdefault(stated_digest_of(content), []).append(name)
    return {digest: names[0] for digest, names in found.items() if len(names) == 1}


def _entry_of(
    identity: InvoiceIdentity,
    *,
    bodies: Mapping[str, bytes],
    by_content: Mapping[str, str],
    reference: str,
) -> str:
    """Which entry of the package this manifest line points at.

    A stated digest is the answer or there is none: falling back to the file
    name when the digest fails to match would reopen the very hole the digest
    closes, because the two disagreeing is exactly when the name is the one
    more likely to be wrong. The name is reached for only when no digest was
    stated at all.
    """
    if identity.content_hash is not None:
        entry = by_content.get(identity.content_hash)
        if entry is not None:
            return entry
        raise ArchiveMetadataUnusable(
            f"_metadata.json of export {reference} gives {identity.ksef_number} a "
            f"digest the package does not carry: {identity.content_hash!r}. "
            f"Refusing to fall back to the file name — a digest that does not "
            f"match is a disagreement, and the name is the weaker half of it."
        )
    if identity.file_name is not None and identity.file_name in bodies:
        return identity.file_name
    raise ArchiveMetadataUnusable(
        f"_metadata.json of export {reference} points {identity.ksef_number} at "
        f"a document the package does not carry: file name "
        f"{identity.file_name!r}. Refusing to archive a package that does not "
        f"match its own manifest."
    )


def located(
    *,
    wanted: tuple[InvoiceIdentity, ...],
    bodies: Mapping[str, bytes],
    reference: str,
) -> dict[str, str]:
    """Pair every manifest line with one entry, and refuse anything left over.

    Returns KSeF number to entry name — the direction the caller reads it in.
    Both refusals still hold: a line pointing at nothing is a manifest the
    package does not match, and an entry no line points at would need a KSeF
    number nobody stated. What changed is how the pairing is made — by the
    digest MF states, or by a file name when no digest was given (GH-87).
    """
    by_content = _by_content(bodies)
    taken: dict[str, str] = {}
    for identity in wanted:
        entry = _entry_of(
            identity,
            bodies=bodies,
            by_content=by_content,
            reference=reference,
        )
        claimed = taken.get(entry)
        if claimed is not None:
            raise ArchiveMetadataUnusable(
                f"_metadata.json of export {reference} points both {claimed} and "
                f"{identity.ksef_number} at {entry!r}. One document cannot be two "
                f"invoices, and guessing which would file one under the other."
            )
        taken[entry] = str(identity.ksef_number)
    unclaimed = sorted(name for name in bodies if name not in taken)
    if unclaimed:
        raise ArchiveMetadataUnusable(
            f"Export {reference} carries {unclaimed[0]!r}, which its "
            f"_metadata.json does not name. Storing it would need a KSeF "
            f"number nobody stated, and skipping it would lose an invoice."
        )
    return {number: entry for entry, number in taken.items()}


def identities(metadata: bytes | None) -> tuple[InvoiceIdentity, ...]:
    """Read the manifest into number/file pairs, or refuse to archive at all."""
    if metadata is None:
        raise ArchiveMetadataUnusable(
            "The package carries no _metadata.json, and that manifest is the "
            "only place a KSeF number comes from. Archiving by the package's "
            "own file names would deduplicate on a name we do not control."
        )
    try:
        document = json.loads(metadata)
    except json.JSONDecodeError as error:
        raise ArchiveMetadataUnusable(
            f"_metadata.json is not JSON: {error.msg} at position {error.pos}."
        ) from error
    return tuple(_identity(entry) for entry in _listed(document))


def _encode_index(
    index: DeduplicationIndex,
    *,
    nip: str,
    environment: KsefEnvironment,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "nip": nip,
        "environment": str(environment),
        "entries": [
            {
                "ksef_number": entry.ksef_number,
                "content_hash": entry.content_hash,
                "archived_at": entry.archived_at.isoformat(),
            }
            for entry in index.entries
        ],
    }


def _decode_index(document: dict[str, object]) -> DeduplicationIndex:
    require_schema(
        document,
        expected=SCHEMA_VERSION,
        named="The deduplication index",
        refused_as=ArchiveIndexUnreadable,
        consequence=("a misread index fetches invoices already held, or hides ones never fetched."),
    )
    entries: list[dict[str, object]] = document["entries"]  # type: ignore[assignment]
    index = DeduplicationIndex()
    for entry in entries:
        # Built through `with_entry` rather than assembled in one go, so a file
        # naming one invoice twice is refused where every other unreadable index
        # is refused, instead of being carried forward as a working one.
        try:
            index = index.with_entry(
                IndexEntry(
                    ksef_number=str(entry["ksef_number"]),
                    content_hash=str(entry["content_hash"]),
                    archived_at=datetime.fromisoformat(str(entry["archived_at"])),
                )
            )
        except IndexEntryAlreadyHeld as repeated:
            raise ArchiveIndexUnreadable(
                f"The deduplication index names one invoice twice: {repeated}"
            ) from repeated
    return index


@dataclass(frozen=True)
class InvoiceArchive:
    """One subject's invoices in their own directory, with the index beside them."""

    nip: str
    environment: KsefEnvironment
    root: Path | None = None
    clock: Callable[[], datetime] = now_utc

    @property
    def directory(self) -> Path:
        # The data directory, never the cache one: a disk cleaner honouring the
        # cache convention would delete the archive the Ministry expects local
        # business operations to run against (D-030, D-032).
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.data_root(override=self.root)

    @property
    def invoice_directory(self) -> Path:
        return self.directory / INVOICE_DIRECTORY

    @property
    def index_path(self) -> Path:
        return self.directory / INDEX_FILE

    def load_index(self) -> DeduplicationIndex:
        if not self.index_path.is_file():
            return DeduplicationIndex()
        return _decode_index(json.loads(self.index_path.read_text(encoding="utf-8")))

    def store(self, *, package: ExportPackage) -> ArchiveReport:
        """Write every invoice the manifest names, once, and record that it was held.

        The index is read, extended and written inside one hold on the subject's
        directory (ADR-107 §2). Read outside it, the snapshot went stale the
        moment a second pass started, and the entries the first pass added were
        written out of existence by the second one's save — leaving invoices on
        disk the index did not know about, fetched again out of an allowance of
        twenty exports an hour (GH-103).
        """
        wanted = identities(package.metadata)
        bodies = {document.name: document.content for document in package.documents}
        carrying = located(wanted=wanted, bodies=bodies, reference=package.reference)
        with exclusive_write(self.directory, directory_mode=ARCHIVE_DIRECTORY_MODE):
            index = self.load_index()
            archived: list[str] = []
            already_held: list[str] = []
            self.invoice_directory.mkdir(mode=ARCHIVE_DIRECTORY_MODE, parents=True, exist_ok=True)
            for identity in wanted:
                number = str(identity.ksef_number)
                if number in index.known:
                    already_held.append(number)
                    continue
                content = bodies[carrying[number]]
                written = self._written(number=number, content=content)
                (archived if written else already_held).append(number)
                index = index.with_entry(
                    IndexEntry(
                        ksef_number=number,
                        content_hash=digest_of(content),
                        archived_at=self.clock(),
                    )
                )
            self._save_index(index)
        return ArchiveReport(
            directory=str(self.invoice_directory),
            index_path=str(self.index_path),
            archived=tuple(archived),
            already_held=tuple(already_held),
        )

    def _written(self, *, number: str, content: bytes) -> bool:
        # The KSeF number is validated as `<NIP>-<date>-<id>-<checksum>`, so it
        # holds neither a separator nor a dot and cannot reach out of the
        # directory or swallow the suffix below.
        target = self.invoice_directory / f"{number}{INVOICE_SUFFIX}"
        if target.exists():
            # Never a silent overwrite (#38). The file name is the identity, so
            # whatever is already there is this very invoice — it is reported as
            # already held rather than replaced.
            return False
        # The rename is the guardian of the invariant (D-006): a crash mid-write
        # leaves a staging file nobody reads, never half an invoice under a name
        # that claims to be a whole one.
        written_atomically(target, content=content, file_mode=ARCHIVE_FILE_MODE)
        return True

    def _save_index(self, index: DeduplicationIndex) -> None:
        document = _encode_index(index, nip=self.nip, environment=self.environment)
        written_atomically(
            self.index_path,
            content=(json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            file_mode=ARCHIVE_FILE_MODE,
        )


@dataclass
class PackageArchivist:
    """Adapts the archive to `PackageRetriever.archive`, and keeps what it reported.

    The retriever takes the archivist as an argument so the export key is
    dropped inside the same operation that stored the invoices (D-033), and its
    callback returns nothing. The report is still worth having, so it is kept
    here rather than thrown away at the call boundary.
    """

    archive: InvoiceArchive
    report: ArchiveReport | None = None

    @property
    def reported(self) -> ArchiveReport:
        """What was stored, for a caller that already knows the archiving ran."""
        if self.report is None:
            raise ArchiveNotPerformed(
                "This archivist has not stored a package yet, so there are no "
                "paths and no KSeF numbers to report. Asking before the "
                "retriever called it means the two ran in the wrong order."
            )
        return self.report

    def __call__(self, package: ExportPackage) -> None:
        self.report = self.archive.store(package=package)
