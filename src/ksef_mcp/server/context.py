"""Who the server acts as, and everything one subject's tools are built from."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ksef_mcp import config, messages
from ksef_mcp.allowance import Allowance
from ksef_mcp.invoices.review import ReviewStore
from ksef_mcp.ksef_port.lazy import load_adapter
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.paths import Nip
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.storage import token_store
from ksef_mcp.storage.archive import InvoiceArchive
from ksef_mcp.storage.audit import AuditTrail, Authorisation, AuthorisationBasis
from ksef_mcp.storage.period_cache import PeriodCache
from ksef_mcp.storage.sync_store import SyncStore

if TYPE_CHECKING:
    from ksef_mcp.ksef_port.adapter import Ksef2Port


def configured_subject() -> config.Configuration:
    """The configuration, with the NIP read in the one spelling we store it under.

    Normalised here rather than in `load_configuration`, which is a leaf every
    other module reads and must not learn about subject identity (GH-111). One
    spelling settled once is what keeps the keyring key and the directory name
    from drifting apart when the file was written with a grouped NIP.
    """
    configuration = config.load_configuration()
    if configuration is None:
        raise NotConfigured(messages.describe_not_configured())
    return replace(configuration, nip=Nip.parsed(configuration.nip).value)


def authenticated_subject() -> tuple[config.Configuration, token_store.StoredToken]:
    """Which taxpayer we act as, and the secret that proves it. Never logged."""
    configuration = configured_subject()
    stored = token_store.read_token(nip=configuration.nip)
    if stored is None:
        raise NotConfigured(
            f"Brak tokenu dla {configuration.nip}. "
            f"Zapisz go: ksef-mcp token set --nip {configuration.nip}"
        )
    return configuration, stored


@dataclass(frozen=True)
class SubjectDependencies:
    """Everything a tool needs for one subject, built from one NIP.

    `Synchroniser` had already recognised this risk and derived its archive from
    its store rather than accept two arguments that must agree. Four tools
    assembled the port, the cache, the stores and the allowance by hand from the
    same pair of values, each free to drift from the others — a subject spelled
    one way for the cache and another for the allowance reads a period it never
    paid for (GH-114).

    Each access builds a fresh store. They are frozen dataclasses holding a NIP,
    an environment and an optional root, so there is nothing to share and
    nothing that would go stale between two tool calls.
    """

    configuration: config.Configuration

    @property
    def nip(self) -> str:
        return self.configuration.nip

    @property
    def environment(self) -> KsefEnvironment:
        return self.configuration.environment

    @property
    def port(self) -> "Ksef2Port":
        return load_adapter()(environment=self.environment)

    @property
    def cache(self) -> PeriodCache:
        return PeriodCache(nip=self.nip, environment=self.environment)

    @property
    def archive(self) -> InvoiceArchive:
        return InvoiceArchive(nip=self.nip, environment=self.environment)

    @property
    def sync_store(self) -> SyncStore:
        return SyncStore(nip=self.nip, environment=self.environment)

    @property
    def review_store(self) -> ReviewStore:
        return ReviewStore(nip=self.nip, environment=self.environment)

    @property
    def allowance(self) -> Allowance:
        return Allowance(nip=self.nip, environment=self.environment)

    @property
    def trail(self) -> AuditTrail:
        return AuditTrail(nip=self.nip, environment=self.environment)

    def authorisation(self, stored: token_store.StoredToken) -> Authorisation:
        """The footing the read stands on — where the proof came from, never the proof."""
        return Authorisation(
            nip=self.nip,
            environment=self.environment,
            basis=AuthorisationBasis.KSEF_TOKEN,
            source=str(stored.source),
        )

    def archive_authorisation(self) -> Authorisation:
        """No token was read: the invoice was already on disk."""
        return Authorisation(
            nip=self.nip,
            environment=self.environment,
            basis=AuthorisationBasis.ARCHIVE,
        )


def authenticated_dependencies() -> tuple[SubjectDependencies, token_store.StoredToken]:
    configuration, stored = authenticated_subject()
    return SubjectDependencies(configuration=configuration), stored
