"""The contract with KSeF, checked against the test registry, not our own double.

Everything the default suite knows about KSeF it knows from
`tests/support/doubles.py` — our own idea of what the registry answers. These
tests ask the registry itself, through the same adapter, allowance and package
code a synchronisation runs, and fail where the answer no longer fits (GH-178).

Every test here reaches the KSeF test registry and is excluded from the default
run by the `ksef_live` marker. Run them on purpose, with a configured subject
on TEST or DEMO and a token in the keyring or in `KSEF_TOKEN`:

    uv run pytest -m ksef_live tests/live --no-cov

or through the manually dispatched `ksef-live` workflow. Production is never a
target: a subject onboarded for it is skipped (CLAUDE.md).

One run spends, at most: one limits read, one metadata query per own role, one
export and `EXPORT_POLL_ATTEMPTS` status checks — all counted by the same
allowance a tool uses. Nothing is retried after a refusal.
"""

import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from ksef_mcp import config
from ksef_mcp.allowance import LimitsReading
from ksef_mcp.clock import now_utc
from ksef_mcp.invoices.package import PackageRetriever
from ksef_mcp.ksef_port.connection import check_period
from ksef_mcp.ksef_port.lazy import load_adapter
from ksef_mcp.ksef_port.protocol import KsefSession
from ksef_mcp.ksef_port.types import (
    ExportPackage,
    ExportState,
    ExportStatus,
    MetadataPage,
    Operation,
    SubjectRole,
)
from ksef_mcp.storage.archive import identities, located
from ksef_mcp.storage.period_cache import MeteredPeriods
from ksef_mcp.storage.sync_store import PendingExport, SyncStore
from tests.support.live import (
    LOOKBACK,
    OWN_ROLES,
    live_configuration,
    live_token,
    readers_for,
)

pytestmark = pytest.mark.ksef_live

# The test registry builds a small package in well under a minute; five minutes
# of patience covers a slow afternoon without turning the run into a poll loop.
EXPORT_POLL_ATTEMPTS = 20
EXPORT_POLL_INTERVAL = timedelta(seconds=15)


@dataclass(frozen=True)
class LiveRegistry:
    configuration: config.Configuration
    session: KsefSession
    readers: MeteredPeriods
    reading: LimitsReading
    root: Path


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveRegistry]:
    """One authenticated session for the whole module, guarded and metered."""
    configuration = live_configuration()
    token = live_token(nip=configuration.nip)
    root = tmp_path_factory.mktemp("kontrakt")
    readers = readers_for(configuration, root=root)
    port = load_adapter()(environment=configuration.environment)
    with port.session(nip=configuration.nip, token=token) as opened:
        session = readers.guarded(session=opened)
        yield LiveRegistry(
            configuration=configuration,
            session=session,
            readers=readers,
            reading=readers.allowance.reading(session=session),
            root=root,
        )
    # The package parts and the period cache hold registry documents.
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def own_pages(registry: LiveRegistry) -> dict[SubjectRole, MetadataPage]:
    period = check_period(moment=now_utc(), window=LOOKBACK)
    reader = registry.readers.reader_for(session=registry.session)
    return {
        role: reader.page_for(session=registry.session, period=period, subject_role=role)
        for role in OWN_ROLES
    }


@dataclass(frozen=True)
class ReadyExport:
    status: ExportStatus
    package: ExportPackage


def polled(*, registry: LiveRegistry, pending: PendingExport) -> ExportStatus:
    budget = registry.readers.allowance.budget(session=registry.session)
    for attempt in range(EXPORT_POLL_ATTEMPTS):
        budget.spend(Operation.EXPORT_STATUS)
        status = registry.session.check_export(handle=pending.handle)
        if status.state is not ExportState.RUNNING:
            return status
        if attempt + 1 < EXPORT_POLL_ATTEMPTS:
            time.sleep(EXPORT_POLL_INTERVAL.total_seconds())
    minutes = EXPORT_POLL_ATTEMPTS * EXPORT_POLL_INTERVAL.total_seconds() / 60
    pytest.skip(f"KSeF nie skończył eksportu {pending.reference} w {minutes:.0f} min")


@pytest.fixture(scope="module")
def ready_export(
    registry: LiveRegistry,
    own_pages: dict[SubjectRole, MetadataPage],
) -> ReadyExport:
    """One package of the subject's own invoices, fetched, decrypted and unpacked.

    Asked for the role that already showed invoices in the metadata, so the
    package is not empty by choice. Held in memory only: the documents are the
    subject's invoices and nothing here has a reason to write them down (D-011).
    """
    role = next((role for role, page in own_pages.items() if page.invoices), None)
    if role is None:
        pytest.skip(f"brak faktur z ostatnich {LOOKBACK.days} dni — eksport byłby pusty")
    moment = now_utc()
    period = check_period(moment=moment, window=LOOKBACK)
    registry.readers.allowance.budget(session=registry.session).spend(Operation.EXPORT)
    handle = registry.session.start_export(period=period, subject_role=role)
    pending = PendingExport.queued(
        handle=handle,
        subject_role=role,
        started_at=moment,
        covering_from=period.date_from,
    )
    status = polled(registry=registry, pending=pending)
    if status.state is ExportState.EMPTY:
        pytest.skip(f"KSeF zamknął eksport {pending.reference} jako pusty")
    assert status.state is ExportState.READY
    retriever = PackageRetriever(
        session=registry.session,
        store=SyncStore(
            nip=registry.configuration.nip,
            environment=registry.configuration.environment,
            root=registry.root,
        ),
    )
    return ReadyExport(
        status=status, package=retriever.collect(export=pending.built(status=status))
    )


def test_the_registry_grants_limits_instead_of_leaving_them_assumed(
    registry: LiveRegistry,
) -> None:
    # A degraded reading means the SDK could not parse `/limits/context`, and
    # the counter fell back to a conservative ceiling — what GH-253 sees on
    # production at every run.
    assert registry.reading.limits.degraded is False


def test_the_metadata_query_answers_for_every_own_role(
    own_pages: dict[SubjectRole, MetadataPage],
) -> None:
    assert set(own_pages) == set(OWN_ROLES)


# The assertions below compare real documents. Each reduces the comparison to a
# boolean or a count first, so a failure prints `assert False` or two numbers,
# never an entry name or a KSeF number (CLAUDE.md, D-038).
def test_a_ready_package_carries_its_manifest(ready_export: ReadyExport) -> None:
    carries = ready_export.package.metadata is not None
    assert carries


def test_the_package_holds_as_many_documents_as_the_registry_counted(
    ready_export: ReadyExport,
) -> None:
    assert len(ready_export.package.documents) == ready_export.status.invoice_count


def test_the_real_manifest_pairs_every_document(ready_export: ReadyExport) -> None:
    # The same pairing the archive runs before writing a byte (GH-87): a key
    # spelled differently than our double spells it refuses the whole package
    # here, with the keys it found in the refusal.
    package = ready_export.package
    bodies = {document.name: document.content for document in package.documents}
    paired = located(
        wanted=identities(package.metadata),
        bodies=bodies,
        reference=package.reference,
    )
    every_document_paired = set(paired) == set(bodies)
    assert every_document_paired
