"""Render an archived invoice into the Ministry's own PDF visualisation (D-027).

The generator is the Ministry's front-end module, run under Node from a
vendored build. Nothing here parses the invoice: the KSeF number carries the
issue date and the taxpayer's NIP, and the verification link is a digest of the
very bytes on disk, so the whole operation reads the file once and never looks
inside it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.preflight import NodeReport, inspect_node

BUNDLE_DIRECTORY: Final[str] = "vendor"

BUNDLE_NAME: Final[str] = "ksef-fe-invoice-converter.1.1.39.js"

SHIM_DIRECTORY: Final[str] = "node"

SHIM_NAME: Final[str] = "render.mjs"

INVOICE_SUFFIX: Final[str] = ".xml"

PDF_SUFFIX: Final[str] = ".pdf"

PDF_FILE_MODE: Final[int] = 0o600

RENDER_TIMEOUT_SECONDS: Final[float] = 120.0

# Only production invoices are verifiable through the public portal, and the
# test and demo registries have no such surface. Inventing a host for them
# would print a link that resolves to nothing onto a document people trust.
VERIFICATION_HOST: Final[str] = "https://qr.ksef.mf.gov.pl/client-app/invoice"

# `<NIP>-<YYYYMMDD>-<identifier>-<checksum>`, validated on the way into the
# archive, so the two leading fields can be read positionally.
KSEF_NUMBER_PARTS: Final[int] = 4


class InvoiceNotArchived(RuntimeError):
    """The invoice was asked for by number and is not on disk under that name."""


class NodeUnavailable(RuntimeError):
    """Node is missing or too old — a degradation, never a failure of the server.

    Raised so the calling tool can say which capability is gone and which
    remain. Everything except the PDF keeps working without Node (D-027).
    """


class GeneratorFailed(RuntimeError):
    """The generator ran and refused the document."""


@dataclass(frozen=True)
class RenderedInvoice:
    ksef_number: str
    path: Path
    byte_count: int
    generator_version: str
    verification_url: str | None


def package_root() -> Path:
    return Path(__file__).parent


def bundle_path() -> Path:
    return package_root() / BUNDLE_DIRECTORY / BUNDLE_NAME


def shim_path() -> Path:
    return package_root() / SHIM_DIRECTORY / SHIM_NAME


def generator_version() -> str:
    """The version the vendored bundle carries, read off its own file name.

    The same string appears in the footer of every document it produces, which
    is what makes a silent substitution impossible (D-027).
    """
    return BUNDLE_NAME.removeprefix("ksef-fe-invoice-converter.").removesuffix(".js")


def issue_date_of(ksef_number: str) -> date:
    parts = ksef_number.split("-")
    if len(parts) != KSEF_NUMBER_PARTS:
        raise InvoiceNotArchived(
            f"{ksef_number!r} nie wygląda na numer KSeF — oczekiwano czterech "
            f"członów rozdzielonych myślnikiem."
        )
    return date.fromisoformat(parts[1])


def nip_of(ksef_number: str) -> str:
    return ksef_number.split("-")[0]


def verification_url(*, ksef_number: str, content: bytes) -> str:
    """The link the portal checks: who issued it, when, and a digest of the bytes.

    The statement writes `VerificationCode` instead of this, on purpose: a link
    is a bearer credential and the CSV is written to be forwarded by e-mail.
    Here the opposite holds — the document this link lands on IS the invoice, it
    never travels anywhere the invoice does not, and the Ministry's own
    visualisation carries the same link beside the QR code. Same three parts,
    different encoding: the portal spells the digest base64url, the code hex.
    """
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
    issued = issue_date_of(ksef_number).strftime("%d-%m-%Y")
    return f"{VERIFICATION_HOST}/{nip_of(ksef_number)}/{issued}/{digest}"


def node_refusal(report: NodeReport) -> str:
    """Why the PDF is unavailable and what still is — the message a user acts on."""
    remains = (
        "Bez Node działa wszystko poza PDF-em: XML faktury, zestawienie CSV i lista w rozmowie."
    )
    required = ".".join(str(part) for part in report.required)
    if report.executable is None or report.version is None:
        return (
            f"Nie znalazłem Node, a generator PDF Ministerstwa wymaga {required}. "
            f"Instalacja: fnm install {required}. Dopisz też `fnm env` do profilu "
            f"powłoki — bez tego .node-version jest deklaracją, nie egzekucją. "
            f"{remains}"
        )
    found = ".".join(str(part) for part in report.version)
    if report.pinned is not None and report.version != report.pinned:
        pinned = ".".join(str(part) for part in report.pinned)
        return (
            f"Node {found} spod {report.executable} nie zgadza się z przypięciem "
            f"{pinned} w {report.pinned_by}. Tak wygląda brak `fnm env` w profilu "
            f"powłoki: plik przypięcia jest, a wersja i tak inna. {remains}"
        )
    return (
        f"Node {found} spod {report.executable} jest starszy niż wymagane "
        f"{required}. Podnieś wersję: fnm install {required}. {remains}"
    )


def run_node(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=RENDER_TIMEOUT_SECONDS,
        check=False,
    )


def stated_failure(stderr: str) -> str:
    """The generator's own complaint, never the document it complained about."""
    for line in reversed(stderr.strip().splitlines()):
        try:
            stated = json.loads(line)
        except ValueError:
            continue
        if isinstance(stated, dict) and "error" in stated:
            return str(stated["error"])
    return "generator nie podał powodu"


@dataclass(frozen=True)
class InvoiceRenderer:
    """Turns one archived invoice into a PDF beside it in the working directory."""

    environment: KsefEnvironment
    archive_directory: Path
    working_directory: Path
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = run_node
    node_report: Callable[..., NodeReport] = inspect_node

    def invoice_path(self, ksef_number: str) -> Path:
        # The number is the file name, so a number that is not a number cannot
        # reach out of the archive — but it is checked before use, not trusted.
        issue_date_of(ksef_number)
        return self.archive_directory / f"{ksef_number}{INVOICE_SUFFIX}"

    def readable_node(self) -> NodeReport:
        report = self.node_report(working_directory=package_root())
        if not report.satisfies_requirement:
            raise NodeUnavailable(node_refusal(report))
        return report

    def __call__(self, ksef_number: str) -> RenderedInvoice:
        source = self.invoice_path(ksef_number)
        if not source.is_file():
            raise InvoiceNotArchived(
                f"Faktury {ksef_number} nie ma w archiwum. Uruchom najpierw "
                f"synchronizację — renderuję wyłącznie to, co już leży na dysku."
            )
        self.readable_node()
        target = self.working_directory / f"{ksef_number}{PDF_SUFFIX}"
        # Only production documents are verifiable, so only they get a link. An
        # empty one leaves the generator's verification block off the page.
        link = (
            verification_url(ksef_number=ksef_number, content=source.read_bytes())
            if self.environment is KsefEnvironment.PRODUCTION
            else None
        )
        completed = self.runner(
            [
                "node",
                str(shim_path()),
                str(bundle_path()),
                str(source),
                str(target),
                ksef_number,
                link or "",
            ]
        )
        if completed.returncode != 0:
            raise GeneratorFailed(
                f"Generator Ministerstwa odrzucił fakturę {ksef_number}: "
                f"{stated_failure(completed.stderr)}"
            )
        target.chmod(PDF_FILE_MODE)
        return RenderedInvoice(
            ksef_number=ksef_number,
            path=target,
            byte_count=target.stat().st_size,
            generator_version=generator_version(),
            verification_url=link,
        )
