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
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from importlib import resources
from pathlib import Path
from typing import Final

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.errors import InvalidKsefIdentifier
from ksef_mcp.ksef_port.types import KsefNumber
from ksef_mcp.preflight import NodeReport, inspect_node
from ksef_mcp.storage import replaced_durably, reserved_staging

PACKAGE_NAME: Final[str] = "ksef_mcp"

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

# The generator's own complaints are short. A cap keeps a chatty message from
# reaching an answer that is meant to carry no invoice content at all — but a
# cap bounds how much of a quotation travels, never whether one can (GH-85).
FAILURE_DETAIL_LIMIT: Final[int] = 200

# How long a run copied out of the invoice has to be before it is redacted.
#
# Not a guess: build 1.1.39 quotes the document today. `Unknown XML Version:
# FA (3)` echoes the form code it read, and the XML parser answers a malformed
# file with `Char: e` — one byte taken straight from it. Neither is personal
# data, but both prove the message is a channel out of the document, and D-011
# does not bend for an artefact we do not control (D-027 lets it be swapped).
#
# Six is where the two demands meet. Below it the guard would eat the
# generator's own words — the invoice carries `Data`, `NIP`, `Nazwa`, and any
# message using them would come back unreadable. At six and above nothing the
# build says today is touched, while a NIP (ten digits), a counterparty's name
# and an invoice number all are. A single echoed character survives, and that
# is the accepted residue: a leak has to carry meaning to be one.
QUOTABLE_RUN_LIMIT: Final[int] = 6

# What the run comparison above cannot see: an identifier the message re-spaced
# on its way out. `98765 43210` is two runs of five, each under the limit and
# each meaningless alone, and together it is somebody's NIP. Length is the only
# shape a taxpayer identifier reliably has, so it is the only one worth naming;
# a counterparty's name re-wrapped the same way is not recoverable by shape and
# is the honest limit of this guard (GH-85).
NIP_LENGTH: Final[int] = 10

SEPARATED_DIGITS: Final[re.Pattern[str]] = re.compile(r"\d[\d\s.,\-]{6,}\d")

# How much of somebody else's message is worth examining at all. The output cap
# below cannot bound this work, because it applies after the guard has run —
# and Node writes multi-line stack traces, so `stderr` is not small by nature.
# Anything past this point would be cut from the answer regardless.
QUOTED_INPUT_LIMIT: Final[int] = 2_000

REDACTED: Final[str] = "[…]"


class InvoiceNotArchived(KsefMcpError):
    """The invoice was asked for by number and is not on disk under that name."""


class NodeUnavailable(KsefMcpError):
    """Node is missing or too old — a degradation, never a failure of the server.

    Raised so the calling tool can say which capability is gone and which
    remain. Everything except the PDF keeps working without Node (D-027).
    """


class GeneratorFailed(KsefMcpError):
    """The generator ran and refused the document."""


@dataclass(frozen=True)
class RenderedInvoice:
    ksef_number: str
    path: Path
    byte_count: int
    generator_version: str
    verification_url: str | None


def package_root() -> Path:
    """Where this package's own files live, asked of the import system.

    `Path(__file__).parent` answers the same thing today, and answers it by
    guessing: it reads an attribute of this module and assumes the resources
    sit beside it. `importlib.resources` asks the loader that actually placed
    the package, which is the component that knows. Converted to `Path`
    because both resources are handed to Node as command-line arguments, and
    a subprocess takes a file name, not a traversable (#106).
    """
    return Path(str(resources.files(PACKAGE_NAME)))


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


def validated(ksef_number: str) -> KsefNumber:
    """Parse the number through the port's own type, which is the only guard here.

    The number becomes a file name on both sides of the render, so this is a
    security boundary rather than a convenience: `KsefNumber`'s pattern is
    anchored and its alphabet is digits and letters, which is what keeps a
    caller-supplied string from carrying `..` or an absolute path into
    `archive_directory / name`. An earlier version of this module counted
    hyphen-separated parts instead and let `../../tmp/x-20260817-y-56` through.
    """
    try:
        return KsefNumber(ksef_number)
    except InvalidKsefIdentifier as rejected:
        raise InvoiceNotArchived(
            f"{ksef_number!r} nie jest numerem KSeF. Oczekiwano kształtu "
            f"<NIP>-<RRRRMMDD>-<identyfikator>-<suma>."
        ) from rejected


def verification_url(*, ksef_number: KsefNumber, content: bytes) -> str:
    """The link the portal checks: who issued it, when, and a digest of the bytes.

    The statement writes `VerificationCode` instead of this, on purpose: a link
    is a bearer credential and the CSV is written to be forwarded by e-mail.
    Here the opposite holds — the document this link lands on IS the invoice, it
    never travels anywhere the invoice does not, and the Ministry's own
    visualisation carries the same link beside the QR code. Same three parts,
    different encoding: the portal spells the digest base64url, the code hex.
    """
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
    issued = ksef_number.assigned_on.strftime("%d-%m-%Y")
    return f"{VERIFICATION_HOST}/{ksef_number.issued_for_nip}/{issued}/{digest}"


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


def without_quotations(stated: str, *, document: str) -> str:
    """Replace everything the complaint copied out of the invoice it refused.

    Read left to right, taking the longest run that also occurs in the
    document. A run at or over `QUOTABLE_RUN_LIMIT` is somebody's data and is
    replaced; anything shorter is a coincidence of letters and is kept, which
    is what leaves the generator's own sentences intact.

    Comparing against the document rather than against a list of permitted
    message shapes is the choice this makes. A permitted-shapes list has to
    predict what the next build will say, and predicting wrongly either lets a
    quotation through or silences a real diagnosis; the document is the one
    thing we can compare against that we already hold.

    Both the file's own text and its entity-decoded form are compared, because
    a build that reads the document into a DOM before complaining would quote
    `&` where the file says `&amp;` — and a comparison against the bytes alone
    would call that a different string. Case is not folded: the file spells
    `version="1.0"` in its declaration, so folding would redact `Version` out
    of `Unknown XML Version`, which is the build's own sentence and the one
    thing the caller needs.
    """
    haystacks = (document, unescape(document))
    kept: list[str] = []
    index = 0
    while index < len(stated):
        run = 0
        while index + run < len(stated) and any(
            stated[index : index + run + 1] in haystack for haystack in haystacks
        ):
            run += 1
        if run < QUOTABLE_RUN_LIMIT:
            kept.append(stated[index])
            index += 1
            continue
        if not kept or kept[-1] != REDACTED:
            kept.append(REDACTED)
        index += run
    return "".join(kept)


def without_separated_identifiers(stated: str, *, document: str) -> str:
    """Redact an identifier of the document that the message spaced out.

    Runs of digits only, and only at NIP length: that is the one field whose
    shape survives re-spacing well enough to be recognised without inventing
    rules for everything else. A date or a quantity keeps its own digits and
    its own meaning, so both stay.
    """
    carried = "".join(character for character in document if character.isdigit())

    def redacted(found: re.Match[str]) -> str:
        run = "".join(character for character in found.group() if character.isdigit())
        if len(run) >= NIP_LENGTH and run in carried:
            return REDACTED
        return found.group()

    return SEPARATED_DIGITS.sub(redacted, stated)


def stated_failure(stderr: str, *, document: str) -> str:
    """The generator's own complaint, never the document it complained about.

    The complaint is somebody else's text on its way to the caller, so it is
    bounded, then stripped of the document, and only then cut to the answer's
    length. Cutting first would halve a quotation and forward the half that
    fit; guarding an unbounded string would do the work on whatever Node chose
    to write (GH-85).
    """
    for line in reversed(stderr.strip().splitlines()):
        try:
            stated = json.loads(line)
        except ValueError:
            continue
        if isinstance(stated, dict) and "error" in stated:
            bounded = str(stated["error"])[:QUOTED_INPUT_LIMIT]
            guarded = without_separated_identifiers(
                without_quotations(bounded, document=document),
                document=document,
            )
            return guarded[:FAILURE_DETAIL_LIMIT]
    return "generator nie podał powodu"


@dataclass(frozen=True)
class InvoiceRenderer:
    """Turns one archived invoice into a PDF beside it in the working directory."""

    environment: KsefEnvironment
    archive_directory: Path
    working_directory: Path
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = run_node
    node_report: Callable[..., NodeReport] = inspect_node

    def invoice_path(self, ksef_number: KsefNumber) -> Path:
        return self.archive_directory / f"{ksef_number}{INVOICE_SUFFIX}"

    def readable_node(self) -> NodeReport:
        report = self.node_report(working_directory=package_root())
        if not report.satisfies_requirement:
            raise NodeUnavailable(node_refusal(report))
        return report

    def __call__(self, asked_for: str) -> RenderedInvoice:
        ksef_number = validated(asked_for)
        source = self.invoice_path(ksef_number)
        if not source.is_file():
            raise InvoiceNotArchived(
                f"Faktury {ksef_number} nie ma w archiwum. Uruchom najpierw "
                f"synchronizację — renderuję wyłącznie to, co już leży na dysku."
            )
        node_executable = self.readable_node().executable
        target = self.working_directory / f"{ksef_number}{PDF_SUFFIX}"
        # Written under a staging name and renamed, like every other file this
        # package produces: Node writes into a file it did not create, so a PDF
        # carrying a counterparty's personal data never exists under the process
        # umask at all.
        # Reserved rather than named after the target: two renders of one
        # invoice used to hand Node the same path, and the second generator
        # truncated the first one's output before either was renamed. The file
        # exists at its final mode before Node opens it, so the umask window the
        # comment above describes is closed too (ADR-107 §3).
        staging = reserved_staging(target, file_mode=PDF_FILE_MODE)
        # Only production documents are verifiable, so only they get a link. An
        # empty one leaves the generator's verification block off the page.
        link = (
            verification_url(ksef_number=ksef_number, content=source.read_bytes())
            if self.environment is KsefEnvironment.PRODUCTION
            else None
        )
        completed = self.runner(
            [
                # The version check above resolved this exact path via
                # `shutil.which`; handing Node's own resolution of the bare
                # `"node"` the final say would let the checked and the run
                # binary diverge under `fnm`, where PATH depends on shell
                # state (GH-160).
                node_executable,
                str(shim_path()),
                str(bundle_path()),
                str(source),
                str(staging),
                str(ksef_number),
                link or "",
            ]
        )
        if completed.returncode != 0:
            # The document is read again here rather than carried down from
            # above: the copy above exists only on production, where the
            # verification link needs it, and a refusal has to be guarded on
            # every environment. This is the error path, so the second read
            # costs nothing anyone waits on.
            #
            # Decoded leniently, because this is the path a broken file takes.
            # The bytes are wanted for one comparison and nothing else, so a
            # `UnicodeDecodeError` here would trade a refusal that names its
            # reason for one that names nothing — the failure this whole branch
            # exists to prevent.
            refused = source.read_bytes().decode("utf-8", errors="replace")
            # Removed here rather than left behind: the name is unique per
            # render now, so nothing would ever reuse or overwrite it, and a
            # working directory filling with half-written PDFs is the price of
            # that uniqueness if the refusal path does not pay it.
            staging.unlink(missing_ok=True)
            raise GeneratorFailed(
                f"Generator Ministerstwa odrzucił fakturę {ksef_number}: "
                f"{stated_failure(completed.stderr, document=refused)}"
            )
        replaced_durably(staging, target)
        return RenderedInvoice(
            ksef_number=str(ksef_number),
            path=target,
            byte_count=target.stat().st_size,
            generator_version=generator_version(),
            verification_url=link,
        )
