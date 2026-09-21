"""One call per tool with nothing between the MCP surface and the services but the port.

Everything in the `test_tools_*` modules substitutes the service itself — the
synchroniser, the lister, the composer, the reviewer — and the sync store with a
lambda that hands back its own arguments. That is the right shape for asking
what the tool surface does, and it leaves the seam below it untested: the tools
build the synchroniser out of a store and let the archive be derived from it,
which is the very construction `test_synchronisation` keeps in place so the two
cannot name different subjects. Stubbed out there, that construction was the one
thing the server tests could not have caught going wrong (GH-176).

Modelled on `tests/cli/test_maintenance.py::test_purge_*`, which was the only
end-to-end run in the suite. The stores write under `tmp_path` already: the
autouse `subject_data_root` fixture substitutes both platform roots in `paths`.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from mcp import Client

from ksef_mcp import config
from ksef_mcp.config import Configuration
from ksef_mcp.invoices.synchronisation import SYNCHRONISED_SUBJECT_ROLES
from ksef_mcp.ksef_port import KsefEnvironment, MetadataPage, Period, SubjectRole
from ksef_mcp.ksef_port import adapter as port_adapter
from ksef_mcp.rendering.node_preflight import NodeReport
from ksef_mcp.rendering.pdf import InvoiceRenderer
from ksef_mcp.server import server, tools_rendering
from ksef_mcp.storage import token_store
from ksef_mcp.storage.archive import InvoiceArchive
from ksef_mcp.storage.audit import AuditTrail
from tests.invoices.test_synchronisation import (
    ScriptedPort,
    ScriptedSession,
    allowances,
    ready,
)
from tests.server.conftest import NIP, recorded_reads
from tests.support.synthetic import synthetic_metadata, synthetic_number


@dataclass
class ServingSession(ScriptedSession):
    """The scripted export session, plus the metadata three of the tools ask for.

    `ScriptedSession` refuses `query_metadata` on purpose — synchronisation goes
    through exports and a pass that queried metadata would be a bug. The listing,
    the review and the statement ask exactly that question, so it is answered
    here rather than by loosening the double they all share.
    """

    served: tuple[object, ...] = ()

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        return MetadataPage(
            invoices=self.served,
            has_more=False,
            truncated=False,
            hwm_date=None,
        )


@pytest.fixture
def subject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Configuration:
    """Configured for real: no service is substituted, only the configuration read."""
    configuration = Configuration(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        keyring_backend="keyring.backends.SecretService.Keyring",
        invoice_directory=tmp_path / "faktury",
    )
    monkeypatch.setattr(config, "load_configuration", lambda: configuration)
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda *, nip: token_store.StoredToken(
            value="tajny-token", source=token_store.TokenSource.KEYRING
        ),
    )
    return configuration


@pytest.fixture
def only_the_port_is_scripted(monkeypatch: pytest.MonkeyPatch, subject: Configuration) -> None:
    session = ServingSession(
        statuses=[ready()],
        limits=allowances(),
        served=(synthetic_metadata(1),),
    )
    monkeypatch.setattr(
        port_adapter,
        "Ksef2Port",
        lambda *, environment: ScriptedPort(session_double=session, environment=environment),
    )


@pytest.fixture
def archive_of(subject: Configuration) -> InvoiceArchive:
    return InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST)


@pytest.mark.anyio
async def test_a_synchronisation_call_puts_the_invoices_on_disk(
    only_the_port_is_scripted: None,
    archive_of: InvoiceArchive,
) -> None:
    async with Client(server, raise_exceptions=True) as client:
        await client.call_tool("synchronise_invoices")

    assert (archive_of.invoice_directory / f"{synthetic_number(1)}.xml").is_file()


@pytest.mark.anyio
async def test_a_synchronisation_call_leaves_its_own_entry_in_the_trail(
    only_the_port_is_scripted: None,
    trail: AuditTrail,
) -> None:
    async with Client(server, raise_exceptions=True) as client:
        await client.call_tool("synchronise_invoices")

    assert recorded_reads(trail)[0].ksef_numbers == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


@pytest.mark.anyio
async def test_a_listing_call_reaches_the_port_and_is_recorded(
    only_the_port_is_scripted: None,
    trail: AuditTrail,
) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("list_recent_invoices")

    listed = called.structured_content["subject_roles"][0]["invoices"]

    assert (listed[0]["ksef_number"], len(recorded_reads(trail))) == (
        str(synthetic_number(1)),
        len(SYNCHRONISED_SUBJECT_ROLES),
    )


@pytest.mark.anyio
async def test_a_review_call_writes_the_ledger_it_names(
    only_the_port_is_scripted: None,
) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("review_new_invoices")

    assert Path(called.structured_content["ledger_file"]).is_file()


@pytest.mark.anyio
async def test_a_statement_call_writes_the_file_it_names(
    only_the_port_is_scripted: None,
    tmp_path: Path,
) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool(
            "export_period_statement",
            {"period": "2026-09", "working_directory": str(tmp_path / "zestawienia")},
        )

    assert Path(called.structured_content["path"]).is_file()


@pytest.mark.anyio
async def test_a_render_call_finds_the_invoice_the_synchronisation_archived(
    monkeypatch: pytest.MonkeyPatch,
    only_the_port_is_scripted: None,
    archive_of: InvoiceArchive,
    tmp_path: Path,
) -> None:
    # The archive is the real one on both sides of this: the number the
    # synchronisation wrote is the number the render is asked for, and nothing
    # in between restates it. Only the generator subprocess stands in — Node is
    # not the seam this is about, and the synthetic body is not an FA(3) the
    # Ministry's build would accept.
    def stub_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        tools_rendering,
        "InvoiceRenderer",
        lambda **kwargs: InvoiceRenderer(
            **kwargs,
            runner=stub_runner,
            node_report=lambda *, working_directory: NodeReport(
                executable="/usr/bin/node",
                version=(22, 17, 0),
                required=(22, 14, 0),
                pinned=None,
                pinned_by=None,
            ),
        ),
    )

    async with Client(server, raise_exceptions=True) as client:
        await client.call_tool("synchronise_invoices")
        called = await client.call_tool(
            "render_invoice_pdf",
            {
                "ksef_number": str(synthetic_number(1)),
                "working_directory": str(tmp_path / "wydruki"),
            },
        )

    assert Path(called.structured_content["path"]).is_file()
