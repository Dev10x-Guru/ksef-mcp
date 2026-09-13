from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.types import (
    ExportHandle,
    ExportPart,
    ExportStatus,
    InvoiceDirection,
    KsefLimits,
    KsefNumber,
    MetadataPage,
    Period,
)


@runtime_checkable
class KsefSession(Protocol):
    """One authenticated conversation with KSeF, in this project's vocabulary.

    Nothing here mentions a client library: the whole point is that swapping
    `ksef2` for a generated client, or for a hand-written one, is a change to a
    single adapter module and to nothing a tool can see.
    """

    def read_limits(self) -> KsefLimits:
        """Report the allowances KSeF grants this context right now."""

    def query_metadata(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
    ) -> MetadataPage:
        """List invoice metadata for a window. Never invoice bodies."""

    def start_export(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
    ) -> ExportHandle:
        """Queue a package export and keep the AES key it was minted with."""

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        """Ask whether the queued package is ready, and where its parts live."""

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        """Return one package part exactly as stored: encrypted, untouched."""

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        """Return one invoice as the exact bytes KSeF holds, for `temp → rename`."""


@runtime_checkable
class KsefPort(Protocol):
    """Opens authenticated sessions. The only thing a caller has to construct.

    The token arrives as an argument. Reading it from the keyring belongs to
    `token_store`, above this boundary: a port that fetches its own secret
    cannot be exercised without one, and on a locked collection the fetch opens
    a prompt that hangs a stdio server (D-004).
    """

    @property
    def environment(self) -> KsefEnvironment:
        """TEST, DEMO or PRODUCTION. Stamped on every answer a tool gives back."""

    def session(self, *, nip: str, token: str) -> AbstractContextManager[KsefSession]:
        """Authenticate and hand out a session, closed when the block ends."""
