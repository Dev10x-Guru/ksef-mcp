import hashlib
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from ksef_mcp import pdf
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.preflight import NodeReport
from synthetic import synthetic_fa3_invoice

KSEF_NUMBER = "1234567890-20260817-0100AB12CD01-56"

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
    assert pdf.issue_date_of(KSEF_NUMBER) == date(2026, 8, 17)


def test_a_number_with_the_wrong_shape_is_refused() -> None:
    with pytest.raises(pdf.InvoiceNotArchived, match="nie wygląda na numer KSeF"):
        pdf.issue_date_of("../../etc/passwd")


def test_nip_is_the_leading_field_of_the_number() -> None:
    assert pdf.nip_of(KSEF_NUMBER) == "1234567890"


def test_verification_url_names_the_issuer_the_date_and_the_bytes() -> None:
    url = pdf.verification_url(ksef_number=KSEF_NUMBER, content=b"faktura")
    assert url.startswith("https://qr.ksef.mf.gov.pl/client-app/invoice/1234567890/17-08-2026/")


def test_verification_url_changes_when_the_bytes_change() -> None:
    first = pdf.verification_url(ksef_number=KSEF_NUMBER, content=b"faktura")
    second = pdf.verification_url(ksef_number=KSEF_NUMBER, content=b"inna faktura")
    assert first != second


def test_verification_digest_carries_no_padding() -> None:
    url = pdf.verification_url(ksef_number=KSEF_NUMBER, content=b"faktura")
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
    assert pdf.stated_failure('{"error": "zły schemat"}') == "zły schemat"


def test_noise_after_the_complaint_is_stepped_over() -> None:
    # Read from the end, so the line that has to be skipped is the trailing one:
    # Node writes the stack trace after the structured complaint.
    assert pdf.stated_failure('{"error": "zły schemat"}\nat Object.<anonymous>') == "zły schemat"


def test_json_that_is_not_a_complaint_is_stepped_over() -> None:
    assert pdf.stated_failure('{"error": "zły schemat"}\n{"warning": "x"}') == "zły schemat"


def test_silence_from_the_generator_is_said_plainly() -> None:
    assert pdf.stated_failure("") == "generator nie podał powodu"


def test_an_invoice_absent_from_the_archive_is_refused(archive: Path, working: Path) -> None:
    render = renderer(archive=archive, working=working)
    with pytest.raises(pdf.InvoiceNotArchived, match="Uruchom najpierw"):
        render("1234567890-20260817-0100AB12CD99-56")


def test_absent_node_stops_the_render_before_it_starts(archive: Path, working: Path) -> None:
    render = renderer(
        archive=archive,
        working=working,
        report=node_report(executable=None, version=None),
    )
    with pytest.raises(pdf.NodeUnavailable):
        render(KSEF_NUMBER)


def test_a_refusing_generator_is_reported_with_its_reason(archive: Path, working: Path) -> None:
    render = renderer(
        archive=archive,
        working=working,
        runner=lambda command: completed(returncode=1, stderr='{"error": "zły schemat"}'),
    )
    with pytest.raises(pdf.GeneratorFailed, match="zły schemat"):
        render(KSEF_NUMBER)


def test_production_invoices_get_a_verification_link(archive: Path, working: Path) -> None:
    seen: list[list[str]] = []

    def capture(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
        return completed()

    render = renderer(archive=archive, working=working, runner=capture)
    rendered = render(KSEF_NUMBER)
    assert rendered.verification_url is not None


def test_test_environment_invoices_get_no_verification_link(
    archive: Path,
    working: Path,
) -> None:
    def capture(command: list[str]) -> subprocess.CompletedProcess[str]:
        Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
        return completed()

    render = renderer(
        archive=archive,
        working=working,
        environment=KsefEnvironment.TEST,
        runner=capture,
    )
    assert render(KSEF_NUMBER).verification_url is None


def test_the_rendered_file_is_readable_only_by_its_owner(archive: Path, working: Path) -> None:
    def capture(command: list[str]) -> subprocess.CompletedProcess[str]:
        Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
        return completed()

    render = renderer(archive=archive, working=working, runner=capture)
    rendered = render(KSEF_NUMBER)
    assert rendered.path.stat().st_mode & 0o777 == pdf.PDF_FILE_MODE


def test_the_result_names_the_generator_version(archive: Path, working: Path) -> None:
    def capture(command: list[str]) -> subprocess.CompletedProcess[str]:
        Path(command[4]).write_bytes(b"%PDF-1.3 udawany")
        return completed()

    render = renderer(archive=archive, working=working, runner=capture)
    assert render(KSEF_NUMBER).generator_version == "1.1.39"


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
