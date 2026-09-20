import hashlib
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from ksef_mcp import pdf, storage
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.preflight import NodeReport
from tests.support.synthetic import BUYER_NAME, SELLER_NIP, synthetic_fa3_invoice

KSEF_NUMBER = "1234567890-20260817-0100AB12CD01-56"

EXAMPLE_INVOICE = synthetic_fa3_invoice().decode("utf-8")

REQUIRED = (22, 14, 0)

# The build the Ministry's portal served on 2026-09-14, byte for byte. Also
# recorded beside the file itself in `vendor/LICENCJA-MF.md`.
BUNDLE_DIGEST = "52210230e5c6ee8d5195541eacb5b81e9cb64fc51845118ec39484d5f7d0fed8"

# The render test drives the real Node binary and the vendored bundle. It is
# the only proof that the shim still matches the generator, so it is skipped
# rather than faked where Node is absent — and CI installs Node so it always
# runs there.
without_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="renderowanie wymaga Node; tu go nie ma",
)


def node_report(
    *,
    executable: str | None = "/usr/bin/node",
    version: tuple[int, int, int] | None = (22, 17, 0),
    pinned: tuple[int, int, int] | None = None,
    pinned_by: Path | None = None,
) -> NodeReport:
    return NodeReport(
        executable=executable,
        version=version,
        required=REQUIRED,
        pinned=pinned,
        pinned_by=pinned_by,
    )


def completed(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["node"], returncode=returncode, stderr=stderr, stdout=""
    )


def writing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Stands in for Node: writes something PDF-shaped where it was told to."""
    Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
    return completed()


def refusing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Fails the test loudly if the renderer reaches Node when it must not."""
    raise AssertionError(f"generator nie powinien zostać wywołany: {command}")


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    directory = tmp_path / "archiwum"
    directory.mkdir()
    (directory / f"{KSEF_NUMBER}.xml").write_bytes(synthetic_fa3_invoice())
    return directory


@pytest.fixture
def working(tmp_path: Path) -> Path:
    directory = tmp_path / "robocze"
    directory.mkdir()
    return directory


def renderer(
    *,
    archive: Path,
    working: Path,
    environment: KsefEnvironment = KsefEnvironment.PRODUCTION,
    runner: object = None,
    report: NodeReport | None = None,
) -> pdf.InvoiceRenderer:
    return pdf.InvoiceRenderer(
        environment=environment,
        archive_directory=archive,
        working_directory=working,
        runner=runner if runner is not None else (lambda command: completed()),
        node_report=lambda *, working_directory: report or node_report(),
    )


def test_generator_version_comes_from_the_bundle_file_name() -> None:
    assert pdf.generator_version() == "1.1.39"


def test_the_vendored_bundle_is_present_in_the_package() -> None:
    assert pdf.bundle_path().is_file()


def test_the_vendored_bundle_is_the_artefact_the_portal_serves() -> None:
    # Not ceremony. A `trailing-whitespace` hook rewrote this file the first
    # time it ran and shortened it by fourteen bytes, which no test then
    # noticed — the shim still loaded and the render still produced a PDF.
    # An edit to somebody else's signed-off build has to fail loudly.
    digest = hashlib.sha256(pdf.bundle_path().read_bytes()).hexdigest()
    assert digest == BUNDLE_DIGEST


def test_the_shim_is_present_in_the_package() -> None:
    assert pdf.shim_path().is_file()


def test_issue_date_is_read_off_the_ksef_number() -> None:
    assert pdf.validated(KSEF_NUMBER).assigned_on == date(2026, 8, 17)


def test_nip_is_the_leading_field_of_the_number() -> None:
    assert pdf.validated(KSEF_NUMBER).issued_for_nip == "1234567890"


# Each of these has exactly four hyphen-separated parts and a second part that
# `date.fromisoformat` accepts, so a validator counting parts let them through
# and `archive_directory / name` then escaped the archive — an absolute part
# discards the archive entirely. The port's anchored pattern is what stops them.
@pytest.mark.parametrize(
    "escape",
    [
        "../../../../tmp/x-20260817-y-56",
        "/etc/passwd-20260817-y-56",
        "..-20260817-y-56",
        "../../etc/passwd",
        "1234567890-20260817-0100AB12CD01-56/../../../etc",
    ],
)
def test_a_number_that_could_reach_out_of_the_archive_is_refused(escape: str) -> None:
    with pytest.raises(pdf.InvoiceNotArchived, match="nie jest numerem KSeF"):
        pdf.validated(escape)


def test_a_traversing_number_never_reaches_the_generator(
    archive: Path,
    working: Path,
) -> None:
    render = renderer(archive=archive, working=working, runner=refusing_runner)

    with pytest.raises(pdf.InvoiceNotArchived):
        render("../../../../tmp/x-20260817-y-56")


def test_verification_url_names_the_issuer_the_date_and_the_bytes() -> None:
    url = pdf.verification_url(ksef_number=pdf.validated(KSEF_NUMBER), content=b"faktura")
    assert url.startswith("https://qr.ksef.mf.gov.pl/client-app/invoice/1234567890/17-08-2026/")


def test_verification_url_changes_when_the_bytes_change() -> None:
    number = pdf.validated(KSEF_NUMBER)
    first = pdf.verification_url(ksef_number=number, content=b"faktura")
    second = pdf.verification_url(ksef_number=number, content=b"inna faktura")
    assert first != second


def test_verification_digest_carries_no_padding() -> None:
    url = pdf.verification_url(ksef_number=pdf.validated(KSEF_NUMBER), content=b"faktura")
    assert "=" not in url.rsplit("/", maxsplit=1)[1]


def test_missing_node_is_explained_with_the_install_command() -> None:
    told = pdf.node_refusal(node_report(executable=None, version=None))
    assert "fnm install 22.14.0" in told


def test_missing_node_names_what_still_works() -> None:
    told = pdf.node_refusal(node_report(executable=None, version=None))
    assert "XML faktury, zestawienie CSV i lista w rozmowie" in told


def test_a_version_differing_from_the_pin_is_read_as_a_missing_fnm_env() -> None:
    told = pdf.node_refusal(
        node_report(version=(20, 11, 0), pinned=(22, 17, 0), pinned_by=Path(".node-version"))
    )
    assert "`fnm env` w profilu powłoki" in told


def test_an_old_node_without_a_pin_is_told_to_upgrade() -> None:
    told = pdf.node_refusal(node_report(version=(20, 11, 0)))
    assert "jest starszy niż wymagane 22.14.0" in told


def test_the_generators_own_complaint_is_quoted() -> None:
    # `document=""` throughout this group on purpose: nothing is a substring of
    # the empty string, so redaction is off and these test the parsing alone.
    assert pdf.stated_failure('{"error": "zły schemat"}', document="") == "zły schemat"


def test_noise_after_the_complaint_is_stepped_over() -> None:
    # Read from the end, so the line that has to be skipped is the trailing one:
    # Node writes the stack trace after the structured complaint.
    stderr = '{"error": "zły schemat"}\nat Object.<anonymous>'
    assert pdf.stated_failure(stderr, document="") == "zły schemat"


def test_json_that_is_not_a_complaint_is_stepped_over() -> None:
    stderr = '{"error": "zły schemat"}\n{"warning": "x"}'
    assert pdf.stated_failure(stderr, document="") == "zły schemat"


def test_silence_from_the_generator_is_said_plainly() -> None:
    assert pdf.stated_failure("", document="") == "generator nie podał powodu"


def test_an_invoice_absent_from_the_archive_is_refused(archive: Path, working: Path) -> None:
    render = renderer(archive=archive, working=working)
    with pytest.raises(pdf.InvoiceNotArchived, match="Uruchom najpierw"):
        render("1234567890-20260817-0100AB12CD99-56")


def test_absent_node_stops_the_render_before_it_starts(archive: Path, working: Path) -> None:
    render = renderer(
        archive=archive,
        working=working,
        runner=refusing_runner,
        report=node_report(executable=None, version=None),
    )
    with pytest.raises(pdf.NodeUnavailable, match=re.escape("fnm install 22.14.0")):
        render(KSEF_NUMBER)


def test_the_checked_node_binary_is_the_one_that_runs(archive: Path, working: Path) -> None:
    """The path `inspect_node` resolved is what runs — not a second, unchecked
    resolution of the literal `"node"`. Under `fnm`, PATH can name a different
    binary than the one the version check already vetted (GH-160)."""
    captured: list[str] = []

    def capturing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        return writing_runner(command)

    render = renderer(
        archive=archive,
        working=working,
        runner=capturing_runner,
        report=node_report(executable="/opt/fnm/node-versions/v22.17.0/bin/node"),
    )
    render(KSEF_NUMBER)

    assert captured[0] == "/opt/fnm/node-versions/v22.17.0/bin/node"


def test_a_refusing_generator_is_reported_with_its_reason(archive: Path, working: Path) -> None:
    render = renderer(
        archive=archive,
        working=working,
        runner=lambda command: completed(returncode=1, stderr='{"error": "zły schemat"}'),
    )
    with pytest.raises(pdf.GeneratorFailed, match="zły schemat"):
        render(KSEF_NUMBER)


@pytest.fixture
def rendered(archive: Path, working: Path) -> pdf.RenderedInvoice:
    return renderer(archive=archive, working=working, runner=writing_runner)(KSEF_NUMBER)


def test_production_invoices_get_a_verification_link(rendered: pdf.RenderedInvoice) -> None:
    assert rendered.verification_url is not None


def test_test_environment_invoices_get_no_verification_link(
    archive: Path,
    working: Path,
) -> None:
    render = renderer(
        archive=archive,
        working=working,
        environment=KsefEnvironment.TEST,
        runner=writing_runner,
    )
    assert render(KSEF_NUMBER).verification_url is None


def test_the_rendered_file_is_readable_only_by_its_owner(rendered: pdf.RenderedInvoice) -> None:
    assert rendered.path.stat().st_mode & 0o777 == pdf.PDF_FILE_MODE


def test_the_render_leaves_no_staging_file_behind(rendered: pdf.RenderedInvoice) -> None:
    assert list(rendered.path.parent.glob(f"*{storage.STAGING_SUFFIX}")) == []


def test_the_result_names_the_generator_version(rendered: pdf.RenderedInvoice) -> None:
    assert rendered.generator_version == "1.1.39"


def test_a_chatty_generator_complaint_is_cut_short() -> None:
    # The cap bounds how much of somebody else's message travels. It is not
    # what keeps the document out — see the quotation tests below.
    stated = pdf.stated_failure(json.dumps({"error": "x" * 500}), document="")
    assert len(stated) <= pdf.FAILURE_DETAIL_LIMIT


def test_a_run_the_document_contains_is_redacted() -> None:
    guarded = pdf.without_quotations("NIP 9876543210 odrzucony", document="…9876543210…")
    assert guarded == f"NIP {pdf.REDACTED} odrzucony"


def test_a_run_too_short_to_carry_meaning_is_kept() -> None:
    # The invoice contains `Data` in half its tag names; redacting on four
    # characters would come back unreadable and tell nobody anything.
    assert pdf.without_quotations("Data", document="<DataWytworzeniaFa>") == "Data"


# The threshold decides every case, so it is pinned from both sides rather than
# sampled: one character either way is the whole difference between a message
# that reads and a message that leaks.
@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ("abcde", "abcde"),
        ("abcdef", pdf.REDACTED),
        ("abcdefg", pdf.REDACTED),
    ],
    ids=["poniżej-progu", "na-progu", "powyżej-progu"],
)
def test_the_threshold_decides_at_exactly_its_own_length(run: str, expected: str) -> None:
    assert pdf.without_quotations(run, document="abcdefg") == expected


def test_a_complaint_that_is_the_whole_document_leaves_nothing_behind() -> None:
    assert pdf.without_quotations(EXAMPLE_INVOICE, document=EXAMPLE_INVOICE) == pdf.REDACTED


def test_an_identifier_the_message_spaced_out_is_still_caught() -> None:
    # Two runs of five, each under the threshold and meaningless alone. The
    # run comparison cannot see this; only the identifier's length can.
    guarded = pdf.stated_failure(
        json.dumps({"error": "Odrzucono NIP 98765 43210"}),
        document=f"<NIP>{SELLER_NIP}</NIP>",
    )
    assert "98765 43210" not in guarded


def test_a_date_the_message_spaced_out_is_left_alone() -> None:
    # Eight digits, not ten: a date is not an identifier, and redacting it
    # would cost the reader the one thing telling them which document failed.
    kept = pdf.without_separated_identifiers("Zła data 2026-09-01", document="2026-09-01")
    assert kept == "Zła data 2026-09-01"


def test_a_value_the_document_spells_as_an_entity_is_still_recognised() -> None:
    # A build reading the file into a DOM quotes `&`; the file says `&amp;`.
    guarded = pdf.without_quotations(
        "Nie rozpoznano: Kowalski & Wspólnicy",
        document="<Nazwa>Kowalski &amp; Wspólnicy</Nazwa>",
    )
    assert "Kowalski" not in guarded


def test_adjacent_quotations_collapse_into_one_marker() -> None:
    # Two runs, not one: the document holds both fields but never side by side,
    # so the greedy walk stops between them and would otherwise emit `[…][…]`.
    guarded = pdf.without_quotations(
        f"{SELLER_NIP}{BUYER_NAME}",
        document=f"{BUYER_NAME} wystawił, NIP {SELLER_NIP}",
    )
    assert guarded == pdf.REDACTED


def test_a_complaint_holding_nothing_of_the_document_travels_whole() -> None:
    # The shape build 1.1.39 answers a malformed file with.
    parser = "Text data outside of root node.\nLine: 0\nColumn: 8\nChar: e"
    assert pdf.without_quotations(parser, document=EXAMPLE_INVOICE) == parser


def test_the_quotation_guard_runs_before_the_cap() -> None:
    # Capping first would cut a quotation in half and forward the half that fit.
    stderr = json.dumps({"error": "x" * 195 + SELLER_NIP})
    assert SELLER_NIP not in pdf.stated_failure(stderr, document=SELLER_NIP)


# GH-85: the cap above bounds how much of a leak travels, never whether one can.
# Two hundred characters of somebody else's message is room enough for a
# counterparty's name and a NIP, and D-011 does not bend for a build we do not
# control. These name the fields of the very invoice being rendered.
@pytest.mark.parametrize(
    ("quoted", "forbidden"),
    [
        (f"Nie rozpoznano nabywcy: {BUYER_NAME}", BUYER_NAME),
        (f"Blad walidacji NIP {SELLER_NIP}", SELLER_NIP),
        ("Odrzucono pozycje: Olej napedowy 100.000 l", "Olej napedowy"),
        ("Zly numer faktury FV/2026/09/0001", "FV/2026/09/0001"),
    ],
    ids=["nabywca", "nip-sprzedawcy", "pozycja", "numer-faktury"],
)
def test_a_generator_quoting_the_document_does_not_leak_it(
    archive: Path,
    working: Path,
    quoted: str,
    forbidden: str,
) -> None:
    render = renderer(
        archive=archive,
        working=working,
        runner=lambda command: completed(returncode=1, stderr=json.dumps({"error": quoted})),
    )

    with pytest.raises(pdf.GeneratorFailed) as refused:
        render(KSEF_NUMBER)

    assert forbidden not in str(refused.value)


@without_node
def test_the_ministrys_generator_refusal_still_says_something(
    refused_archive: Path,
    working: Path,
) -> None:
    """The guard has to leave a reason behind, or it has only traded failures.

    Redacting everything would pass the test above and tell the caller
    nothing — the state GH-84 was filed about. Asserting the build's exact
    wording would break on a swap it is entitled to make, so this asserts the
    one thing that must hold either way: the answer is not the silence
    `stated_failure` falls back to when it finds no complaint at all.
    """
    render = pdf.InvoiceRenderer(
        environment=KsefEnvironment.PRODUCTION,
        archive_directory=refused_archive,
        working_directory=working,
    )

    with pytest.raises(pdf.GeneratorFailed) as refused:
        render(KSEF_NUMBER)

    assert "generator nie podał powodu" not in str(refused.value)


@without_node
def test_the_ministrys_generator_renders_an_archived_invoice(
    archive: Path,
    working: Path,
) -> None:
    render = pdf.InvoiceRenderer(
        environment=KsefEnvironment.PRODUCTION,
        archive_directory=archive,
        working_directory=working,
    )
    rendered = render(KSEF_NUMBER)
    assert rendered.path.read_bytes().startswith(b"%PDF")


@without_node
def test_the_rendered_document_is_not_empty(archive: Path, working: Path) -> None:
    render = pdf.InvoiceRenderer(
        environment=KsefEnvironment.PRODUCTION,
        archive_directory=archive,
        working_directory=working,
    )
    assert render(KSEF_NUMBER).byte_count > 10_000


@pytest.fixture
def refused_archive(tmp_path: Path) -> Path:
    """A real FA(3) the generator will refuse, with its data left intact.

    Only the form code is broken, and only that one, because the build accepts
    a wrong `WariantFormularza`, an unknown namespace and even a truncated
    document — this is the corruption it actually answers with a refusal.
    Every field a leak would carry (the NIP, the counterparty, the line item)
    is still in the bytes it reads; a fixture that corrupted the data instead
    would prove nothing about what the message may quote.
    """
    directory = tmp_path / "archiwum-odrzucone"
    directory.mkdir()
    spoiled = synthetic_fa3_invoice().replace(
        b'kodSystemowy="FA (3)"',
        b'kodSystemowy="FA (99)"',
    )
    (directory / f"{KSEF_NUMBER}.xml").write_bytes(spoiled)
    return directory


@without_node
@pytest.mark.parametrize(
    "forbidden",
    [SELLER_NIP, BUYER_NAME, "FV/2026/09/0001", "Olej napędowy", "Testowa"],
    ids=["nip-sprzedawcy", "nabywca", "numer-faktury", "pozycja", "adres"],
)
def test_the_ministrys_generator_refusal_quotes_nothing_from_the_document(
    refused_archive: Path,
    working: Path,
    forbidden: str,
) -> None:
    """GH-85: the contract the vendored build must keep, checked against the build.

    The bundle is somebody else's artefact and D-027 lets it be swapped. Its
    refusal text reaches the caller, so every swap has to re-prove that the
    text carries none of the document — a cap on its length never could.
    """
    render = pdf.InvoiceRenderer(
        environment=KsefEnvironment.PRODUCTION,
        archive_directory=refused_archive,
        working_directory=working,
    )

    with pytest.raises(pdf.GeneratorFailed) as refused:
        render(KSEF_NUMBER)

    assert forbidden not in str(refused.value)
