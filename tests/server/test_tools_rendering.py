import subprocess
from pathlib import Path

import pytest
from mcp import Client

from ksef_mcp import config
from ksef_mcp.config import Configuration
from ksef_mcp.rendering.node_preflight import NodeReport
from ksef_mcp.rendering.pdf import InvoiceNotArchived, InvoiceRenderer
from ksef_mcp.server import context, server, tools_rendering
from ksef_mcp.server.app import Journal
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.server.results import RenderedInvoiceResult
from ksef_mcp.server.tools_rendering import render_invoice
from ksef_mcp.storage.audit import (
    PDF_FORMAT,
    AuditedOperation,
    AuditTrail,
    AuthorisationBasis,
    Disclosure,
)
from tests.server.conftest import RENDER_NUMBER, recorded_reads
from tests.support.synthetic import synthetic_fa3_invoice

# Renderowanie PDF (#42). Narzędzie nie sięga do KSeF — otwiera to, co archiwum
# już trzyma — więc jego testy nie potrzebują tokenu ani atrapy portu.


def rendered_invoice(
    *,
    ksef_number: str = RENDER_NUMBER,
    working_directory: str | None = None,
) -> RenderedInvoiceResult:
    return render_invoice(
        ksef_number=ksef_number,
        working_directory=working_directory,
        journal=Journal(operation=AuditedOperation.RENDER),
    )


@pytest.fixture
def archived_invoice(configured: Configuration, tmp_path: Path) -> Path:
    directory = tmp_path / "archiwum"
    directory.mkdir()
    (directory / f"{RENDER_NUMBER}.xml").write_bytes(synthetic_fa3_invoice())
    return directory


@pytest.fixture
def rendering(monkeypatch: pytest.MonkeyPatch, archived_invoice: Path) -> None:
    class StubArchive:
        def __init__(self, **kwargs: object) -> None:
            self.invoice_directory = archived_invoice

    def stub_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(context, "InvoiceArchive", StubArchive)
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


@pytest.fixture
def rendered(rendering: None) -> RenderedInvoiceResult:
    return rendered_invoice()


def test_rendering_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        rendered_invoice()


def test_the_answer_points_at_the_document_it_wrote(rendered: RenderedInvoiceResult) -> None:
    assert Path(rendered.path).is_file()


def test_the_answer_names_the_invoice_it_opened(rendered: RenderedInvoiceResult) -> None:
    assert rendered.ksef_number == RENDER_NUMBER


def test_the_answer_names_the_generator_build(rendered: RenderedInvoiceResult) -> None:
    assert rendered.generator_version == "1.1.39"


def test_the_render_explains_itself_under_the_same_name_as_every_other_tool(
    rendered: RenderedInvoiceResult,
) -> None:
    # The one answer of the five that carried no status sentence at all (GH-171).
    assert rendered.message == (
        f"Dokument zapisany w {rendered.path}. Rozmiar: {rendered.byte_count} B. Generator: 1.1.39."
    )


def test_a_test_environment_document_carries_no_verification_link(
    rendered: RenderedInvoiceResult,
) -> None:
    assert rendered.verification_url is None


def test_the_answer_repeats_the_environment_it_acted_in(rendered: RenderedInvoiceResult) -> None:
    assert rendered.environment == "test"


def test_a_caller_may_declare_the_directory_for_one_render(
    rendering: None,
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "gdzie indziej"

    result = rendered_invoice(working_directory=str(elsewhere))

    assert Path(result.path).parent == elsewhere


def test_a_cloud_synced_directory_is_named_before_the_pdf_leaves(
    rendering: None,
    tmp_path: Path,
) -> None:
    result = rendered_invoice(working_directory=str(tmp_path / "OneDrive" / "faktury"))

    assert "onedrive" in result.warnings[0]


def test_a_private_directory_leaves_the_render_answer_without_caveats(
    rendered: RenderedInvoiceResult,
) -> None:
    assert rendered.warnings == []


def test_an_unsynchronised_invoice_is_refused_rather_than_fetched(rendering: None) -> None:
    with pytest.raises(InvoiceNotArchived, match="Uruchom najpierw"):
        rendered_invoice(ksef_number="1234567890-20260817-0100AB12CD99-56")


def test_a_render_records_a_disk_disclosure(
    rendered: RenderedInvoiceResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].disclosure is Disclosure.DISK


def test_a_render_records_the_invoice_it_opened(
    rendered: RenderedInvoiceResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].ksef_numbers == (RENDER_NUMBER,)


def test_a_render_records_the_format_it_produced(
    rendered: RenderedInvoiceResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].formats == (PDF_FORMAT,)


def test_a_render_records_that_no_token_was_needed(
    rendered: RenderedInvoiceResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].authorisation.basis is AuthorisationBasis.ARCHIVE


@pytest.mark.anyio
async def test_the_render_tool_answers_with_a_path_but_no_invoice(rendering: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("render_invoice_pdf", {"ksef_number": RENDER_NUMBER})

    assert called.structured_content["path"].endswith(f"{RENDER_NUMBER}.pdf")
