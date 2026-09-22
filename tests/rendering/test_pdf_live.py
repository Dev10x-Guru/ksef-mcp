"""The render checked against a real invoice, and against the portal's own PDF.

Every test here reaches the KSeF test registry and is excluded from the default
run by the `ksef_live` marker. Run them on purpose, with a configured subject
and a token in the keyring or in `KSEF_TOKEN`:

    uv run pytest -m ksef_live tests/rendering/test_pdf_live.py

The comparison with the portal takes the portal's PDF from `KSEF_PORTAL_PDF`,
saved by hand from the Ministry's application for the same invoice the test
downloads. Nothing here fetches it: the portal is a channel without a contract
(D-027, ADR-112), and a test that scraped it would fail with the portal rather
than with the render. What the comparison proves is agreement with the build
the portal runs — never that the layout is any good (D-027).
"""

import os
import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from pypdf import PdfReader

from ksef_mcp import config, ksef_port
from ksef_mcp.allowance import Allowance
from ksef_mcp.clock import now_utc
from ksef_mcp.ksef_port.connection import check_period
from ksef_mcp.ksef_port.lazy import load_adapter
from ksef_mcp.ksef_port.types import (
    InvoiceMetadata,
    KsefEnvironment,
    SubjectRole,
)
from ksef_mcp.rendering import pdf
from ksef_mcp.storage import token_store
from ksef_mcp.storage.period_cache import MeteredPeriods, PeriodCache

pytestmark = pytest.mark.ksef_live

PORTAL_PDF_VARIABLE = "KSEF_PORTAL_PDF"

# The window `verify` asks about: wide enough to hold a purchase on the test
# registry, well under the port's ceiling, and one query from the hourly
# twenty (D-031).
LOOKBACK = timedelta(days=30)

# The roles a subject holds on its own invoices. Third parties and authorised
# subjects are rarer on a test account, and each role asked is one more query.
OWN_ROLES = (SubjectRole.BUYER, SubjectRole.SELLER)


def live_configuration() -> config.Configuration:
    """The subject this machine was onboarded for, or a skip saying why not.

    Read from the real configuration path, not the one the suite's autouse
    fixture redirects: this test wants the subject a person set up, and a
    missing one is a reason to skip rather than a failure of the render.
    """
    configuration = config.load_configuration()
    if configuration is None:
        pytest.skip("brak konfiguracji — uruchom `ksef-mcp onboarding`")
    if configuration.environment is KsefEnvironment.PRODUCTION:
        # Never from a test (CLAUDE.md): production limits are a tenth of test
        # and the Ministry logs every breach against the subject.
        pytest.skip("podmiot skonfigurowany na produkcję — test na żywo idzie tylko na test/demo")
    return configuration


def live_token(*, nip: str) -> token_store.StoredToken:
    stored = token_store.read_token(nip=nip)
    if stored is None:
        pytest.skip(f"brak tokenu dla {nip} — `ksef-mcp token set` albo KSEF_TOKEN")
    return stored


def readers_for(configuration: config.Configuration, *, root: Path) -> MeteredPeriods:
    """The same metered pairing `verify` uses, rooted under the test's directory.

    Rooted explicitly rather than through the autouse redirect, because the
    fixture below is module-scoped and the redirect is not. The counter still
    counts — one query per role at most — it just counts into a directory that
    disappears with the run.
    """
    return MeteredPeriods(
        cache=PeriodCache(nip=configuration.nip, environment=configuration.environment, root=root),
        allowance=Allowance(
            nip=configuration.nip,
            environment=configuration.environment,
            data_root=root,
            cache_root=root,
        ),
    )


def first_invoice(
    *,
    session: ksef_port.KsefSession,
    readers: MeteredPeriods,
) -> InvoiceMetadata | None:
    period = check_period(moment=now_utc(), window=LOOKBACK)
    reader = readers.reader_for(session=session)
    for role in OWN_ROLES:
        page = reader.page_for(session=session, period=period, subject_role=role)
        if page.invoices:
            return page.invoices[0]
    return None


@pytest.fixture(scope="module")
def live_archive(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Path, InvoiceMetadata]]:
    """One real invoice from the test registry, on disk under its KSeF number.

    Fetched once for the module: the metadata query and the download each
    spend from the hourly allowance, and every assertion below reads the
    result. Skips, rather than fails, when the subject has nothing recent —
    an empty test account says nothing about the render.

    Removed when the module is done. The generator reads a file, so the
    document has to touch the disk — but pytest keeps its last few temporary
    directories, and a registry document is not something to leave lying
    outside the subject's own archive (D-011).
    """
    configuration = live_configuration()
    token = live_token(nip=configuration.nip)
    if shutil.which("node") is None:
        pytest.skip("renderowanie wymaga Node; tu go nie ma")
    root = tmp_path_factory.mktemp("na-zywo")
    readers = readers_for(configuration, root=root)
    port = load_adapter()(environment=configuration.environment)
    with port.session(nip=configuration.nip, token=token) as opened:
        session = readers.guarded(session=opened)
        found = first_invoice(session=session, readers=readers)
        if found is None:
            pytest.skip(
                f"brak faktur z ostatnich {LOOKBACK.days} dni na {configuration.environment}"
            )
        body = session.download_invoice(ksef_number=found.ksef_number)
    archive = root / "archiwum"
    archive.mkdir()
    (archive / f"{found.ksef_number}{pdf.INVOICE_SUFFIX}").write_bytes(body)
    yield archive, found
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def rendered_live(
    live_archive: tuple[Path, InvoiceMetadata],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[pdf.RenderedInvoice]:
    archive, found = live_archive
    working = tmp_path_factory.mktemp("robocze")
    render = pdf.InvoiceRenderer(
        environment=live_configuration().environment,
        archive_directory=archive,
        working_directory=working,
    )
    yield render(str(found.ksef_number))
    # The PDF names the counterparty as plainly as the XML did.
    shutil.rmtree(working, ignore_errors=True)


def page_texts(path: Path) -> tuple[str, ...]:
    return tuple(page.extract_text() for page in PdfReader(path).pages)


# Every assertion below compares a real document, and pytest's introspection
# prints both sides of a failed `in` or `==` — the counterparty's name, NIP and
# amounts would land in the terminal. So each test reduces the comparison to
# a boolean or to page numbers first and asserts on that: a failure then reads
# `assert False` or `[1]`, never the invoice (CLAUDE.md: treść faktury nie
# trafia do logów ani komunikatów błędów).
def differing_pages(ours: tuple[str, ...], theirs: tuple[str, ...]) -> list[int]:
    return [
        number
        for number, (our_page, their_page) in enumerate(zip(ours, theirs, strict=True), start=1)
        if our_page != their_page
    ]


@pytest.fixture(scope="module")
def rendered_live_text(rendered_live: pdf.RenderedInvoice) -> str:
    return "\n".join(page_texts(rendered_live.path))


def test_a_real_invoice_renders_to_a_document(rendered_live: pdf.RenderedInvoice) -> None:
    assert rendered_live.path.read_bytes().startswith(b"%PDF")


def test_the_rendered_real_document_is_not_empty(rendered_live: pdf.RenderedInvoice) -> None:
    assert rendered_live.byte_count > 10_000


# Every value is one KSeF reported in the metadata for the same invoice, read
# back from the page. The synthetic render test checks what the fixture put in;
# this one checks what the registry says the document holds, on a document no
# builder in this repository produced (GH-82).
@pytest.mark.parametrize(
    "field",
    ["ksef_number", "seller_invoice_number", "seller_nip"],
    ids=["numer-ksef", "numer-faktury", "nip-sprzedawcy"],
)
def test_the_rendered_real_document_shows_what_the_registry_reported(
    live_archive: tuple[Path, InvoiceMetadata],
    rendered_live_text: str,
    field: str,
) -> None:
    _, found = live_archive
    shown = str(getattr(found, field)) in rendered_live_text
    assert shown


def test_the_rendered_real_document_names_the_generator_build(rendered_live_text: str) -> None:
    # The footer is where a swapped bundle shows itself (D-027).
    named = pdf.generator_version() in rendered_live_text
    assert named


@pytest.fixture(scope="module")
def portal_pdf() -> Path:
    """The Ministry's own rendering of the same invoice, saved by hand.

    An environment variable rather than a file in the repository: a real
    invoice, even from the test registry, is not something to commit (D-011),
    and the portal is not something to scrape (ADR-112).
    """
    named = os.environ.get(PORTAL_PDF_VARIABLE)
    if not named:
        pytest.skip(f"brak {PORTAL_PDF_VARIABLE} — wskaż PDF pobrany z portalu MF dla tej faktury")
    path = Path(named).expanduser()
    if not path.is_file():
        pytest.skip(f"{PORTAL_PDF_VARIABLE}={path} nie wskazuje pliku")
    return path


def test_the_render_has_as_many_pages_as_the_portal(
    rendered_live: pdf.RenderedInvoice,
    portal_pdf: Path,
) -> None:
    assert len(PdfReader(rendered_live.path).pages) == len(PdfReader(portal_pdf).pages)


def test_the_render_is_the_size_of_the_portals(
    rendered_live: pdf.RenderedInvoice,
    portal_pdf: Path,
) -> None:
    # Same size, not same bytes: the document carries a creation timestamp and
    # an identifier, so the digests differ while the length does not (D-027).
    assert rendered_live.byte_count == portal_pdf.stat().st_size


def test_the_render_says_what_the_portal_says(
    rendered_live: pdf.RenderedInvoice,
    portal_pdf: Path,
) -> None:
    assert differing_pages(page_texts(rendered_live.path), page_texts(portal_pdf)) == []
