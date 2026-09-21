"""One archived invoice, written as the PDF the Ministry's own application shows."""

from pathlib import Path

from ksef_mcp.invoices.statement import prepare_working_directory
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.rendering.pdf import InvoiceRenderer, RenderedInvoice, render_entries
from ksef_mcp.server.app import Journal, reported, server
from ksef_mcp.server.context import SubjectDependencies, configured_subject
from ksef_mcp.server.results import RenderedInvoiceResult
from ksef_mcp.storage.audit import AuditedOperation


def describe_rendering(rendered: RenderedInvoice) -> str:
    """Names the file and the build that wrote it, and nothing not already here."""
    return (
        f"Dokument zapisany w {rendered.path}. Rozmiar: {rendered.byte_count} B. "
        f"Generator: {rendered.generator_version}."
    )


def describe_rendered(
    rendered: RenderedInvoice,
    *,
    nip: str,
    environment: KsefEnvironment,
    warnings: tuple[str, ...],
) -> RenderedInvoiceResult:
    return RenderedInvoiceResult(
        nip=nip,
        environment=str(environment),
        message=describe_rendering(rendered),
        ksef_number=rendered.ksef_number,
        path=str(rendered.path),
        byte_count=rendered.byte_count,
        generator_version=rendered.generator_version,
        verification_url=rendered.verification_url,
        warnings=list(warnings),
    )


def render_invoice(
    *,
    ksef_number: str,
    working_directory: str | None,
    journal: Journal,
) -> RenderedInvoiceResult:
    subject = SubjectDependencies(configuration=configured_subject())
    directory = (
        subject.configuration.working_directory
        if working_directory is None
        else Path(working_directory).expanduser()
    )
    working = prepare_working_directory(directory)
    renderer = InvoiceRenderer(
        environment=subject.environment,
        archive_directory=subject.archive.invoice_directory,
        working_directory=working.path,
    )
    rendered = renderer(ksef_number)
    trail = subject.trail
    # No token was needed and none was read: the invoice was already on disk.
    # The trail still says under whose NIP and environment it was opened.
    journal.record(
        trail=trail,
        entries=render_entries(
            rendered,
            authorisation=subject.archive_authorisation(),
            moment=trail.clock(),
        ),
    )
    return describe_rendered(
        rendered,
        nip=subject.nip,
        environment=subject.environment,
        warnings=working.warnings,
    )


@server.tool()
def render_invoice_pdf(
    ksef_number: str,
    working_directory: str | None = None,
) -> RenderedInvoiceResult:
    """Write one archived invoice as the PDF the Ministry's own application shows.

    `ksef_number` names an invoice already in the archive. Nothing is fetched:
    this call spends none of the twenty metadata queries an hour and works with
    no network at all. An invoice that has not been synchronised yet is refused
    rather than downloaded, so the answer never depends on a query budget.

    The document is produced by the Ministry's own generator, run locally from a
    build vendored with this package — the same code the verification portal
    loads into a browser. Fidelity is therefore official rather than
    approximate, and `generator_version` names the build, the same string the
    footer of the document carries.

    `working_directory` overrides the directory declared during onboarding for
    this one call, under the same rules as the statement: created `0700` if new,
    refused inside the cache or data root, and reported in `warnings` when its
    path looks like a cloud sync folder or its permissions are wider than `0700`.
    The PDF names the counterparty exactly as the statement does.

    On production the document carries the QR code and verification link, and
    `verification_url` repeats it here. Test and demo invoices have no
    verification surface, so both are absent rather than pointing at a page that
    would not resolve.

    Requires Node — `uvx` cannot install it and a Python package cannot depend
    on it. Without Node this one call fails with a message saying what to
    install; synchronisation, the CSV statement and the listing are unaffected.

    The generator accepts FA(1), FA(2), FA(3), UPO and PEF, but only FA(3) has
    been exercised end to end. Treat a refusal on an older schema as untested
    rather than impossible, and report it.
    """
    with reported(AuditedOperation.RENDER) as journal:
        return render_invoice(
            ksef_number=ksef_number,
            working_directory=working_directory,
            journal=journal,
        )
