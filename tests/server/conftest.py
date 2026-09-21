"""What every tool's tests share: the subject, the token, the trail, the client."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import CallToolResult, ListToolsResult

from ksef_mcp import config
from ksef_mcp.allowance import CeilingNotice
from ksef_mcp.config import Configuration
from ksef_mcp.invoices.synchronisation import (
    SubjectRoleReport,
    SynchronisationReport,
    SyncOutcome,
)
from ksef_mcp.ksef_port.types import KsefEnvironment, SubjectRole
from ksef_mcp.server import server
from ksef_mcp.storage import token_store
from ksef_mcp.storage.audit import AuditEntry, AuditTrail

NIP = "1234567890"

REACHED = datetime(2026, 9, 10, tzinfo=UTC)

ARCHIVED_NUMBER = "1234567890-20260901-0100AB12CD01-56"

HELD_NUMBER = "1234567890-20260901-0100AB12CD02-56"

ARCHIVE_DIRECTORY = "/dane/subjects/1234567890/test/invoices"

RENDER_NUMBER = "1234567890-20260817-0100AB12CD01-56"

GRANTED_CEILINGS = CeilingNotice(
    max_invoice_megabytes=1,
    max_invoice_with_attachment_megabytes=3,
    max_invoices_per_session=10_000,
    assumed=False,
)

ASSUMED_CEILINGS = replace(GRANTED_CEILINGS, assumed=True)


class StubSynchroniser:
    """Stands in for the pass itself: this package's job is the tool surface."""

    def __init__(self, *, port: object, store: object, allowance: object) -> None:
        self.port = port
        self.store = store
        self.allowance = allowance

    def run(self, *, nip: str, token: str) -> SynchronisationReport:
        return SynchronisationReport(
            subject_roles=(
                SubjectRoleReport(
                    subject_role=SubjectRole.BUYER,
                    outcome=SyncOutcome.ARCHIVED,
                    message="Paczka EXP-1 trafiła do archiwum.",
                    invoice_count=7,
                    part_count=1,
                    reached=REACHED,
                    archived=(ARCHIVED_NUMBER,),
                    already_held=(HELD_NUMBER,),
                    archive_directory=ARCHIVE_DIRECTORY,
                ),
                SubjectRoleReport(
                    subject_role=SubjectRole.THIRD_SUBJECT,
                    outcome=SyncOutcome.NOT_DUE,
                    message="Za wcześnie na kolejny eksport dla tego typu podmiotu.",
                ),
            ),
            pending_exports=("EXP-1",),
            state_path="/dane/synchronisation.json",
            ceilings=GRANTED_CEILINGS,
        )


async def failing_call(tool: str, arguments: dict[str, str] | None = None) -> CallToolResult:
    async with Client(server) as client:
        return await client.call_tool(tool, arguments)


def recorded_reads(trail: AuditTrail) -> tuple[AuditEntry, ...]:
    return trail.entries()


@pytest.fixture
async def listed_tools() -> ListToolsResult:
    async with Client(server, raise_exceptions=True) as client:
        return await client.list_tools()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Configuration:
    configuration = Configuration(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        keyring_backend="keyring.backends.SecretService.Keyring",
        working_directory=tmp_path,
    )
    monkeypatch.setattr(config, "load_configuration", lambda: configuration)
    return configuration


@pytest.fixture
def with_a_token(monkeypatch: pytest.MonkeyPatch, configured: Configuration) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda *, nip: token_store.StoredToken(
            value="tajny-token", source=token_store.TokenSource.KEYRING
        ),
    )


@pytest.fixture
def trail(subject_data_root: Path) -> AuditTrail:
    return AuditTrail(nip=NIP, environment=KsefEnvironment.TEST, root=subject_data_root)
