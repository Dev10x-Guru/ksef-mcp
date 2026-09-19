"""The acceptance criterion of GH-35: a second client could take over.

Both implementations of `KsefPort` — the one over `ksef2` and one holding
invoices in a list — are driven through the same application service and the
same assertions. If the port ever leaks an SDK type, the in-memory side stops
being writable without importing `ksef2`, and these tests go red.
"""

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import ksef_mcp
from conftest import an_allowance
from doubles import HWM
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    Credential,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    ExportStatus,
    InvoiceMetadata,
    KsefLimits,
    KsefNumber,
    KsefPort,
    KsefSession,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
    SubjectRole,
    check_connection,
)
from ksef_mcp.ksef_port.adapter import Ksef2Port
from ksef_mcp.period_cache import MeteredPeriods, PeriodCache
from synthetic import synthetic_metadata
from test_port_adapter import NIP, TOKEN, Plan

PORT_PACKAGE = Path(ksef_mcp.__file__).parent / "ksef_port"

ALLOWED_TO_SEE_THE_SDK = "adapter.py"


@dataclass
class InMemoryKsefSession:
    """A second client, in the only form a test can own end to end."""

    invoices: list[InvoiceMetadata]
    bodies: dict[str, bytes] = field(default_factory=dict)
    parts: dict[str, bytes] = field(default_factory=dict)

    def read_limits(self) -> KsefLimits:
        return KsefLimits(
            rates=RateLimits(
                metadata_queries=OperationLimit(per_second=8, per_minute=16, per_hour=20),
                exports=OperationLimit(per_second=2, per_minute=4, per_hour=20),
                export_statuses=OperationLimit(per_second=8, per_minute=16, per_hour=200),
                invoice_downloads=OperationLimit(per_second=4, per_minute=16, per_hour=64),
            ),
            ceilings=SessionCeilings(
                max_invoice_megabytes=1,
                max_invoice_with_attachment_megabytes=3,
                max_invoices_per_session=10_000,
            ),
        )

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        return MetadataPage(
            invoices=tuple(self.invoices),
            has_more=False,
            truncated=False,
            hwm_date=HWM,
        )

    def start_export(self, *, period: Period, subject_role: SubjectRole) -> ExportHandle:
        return ExportHandle(
            reference="EXP-1",
            encryption=ExportEncryption(key=b"k" * 32, initialisation_vector=b"i" * 16),
        )

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        return ExportStatus(
            state=ExportState.READY,
            parts=(
                ExportPart(
                    ordinal=1,
                    name="package_part_1.zip.aes",
                    method="GET",
                    url="https://storage.example/part/1",
                    size_bytes=1024,
                    content_hash="c3BsaXQ=",
                    encrypted_size_bytes=1040,
                    encrypted_content_hash="ZW5jcnlwdGVk",
                ),
            ),
            truncated=False,
            hwm_date=HWM,
            last_permanent_storage_date=None,
            invoice_count=len(self.invoices),
        )

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        return self.parts.get(part.url, b"\x00encrypted")

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        return self.bodies.get(ksef_number.value, b"<Faktura/>")


@dataclass
class InMemoryKsefPort:
    environment: KsefEnvironment
    invoices: list[InvoiceMetadata] = field(default_factory=list)

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[InMemoryKsefSession]:
        yield InMemoryKsefSession(invoices=list(self.invoices))


@pytest.fixture(params=["ksef2", "in-memory"])
def port(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> KsefPort:
    if request.param == "in-memory":
        return InMemoryKsefPort(
            environment=KsefEnvironment.TEST,
            invoices=[synthetic_metadata(1), synthetic_metadata(2)],
        )
    Plan().install(monkeypatch)
    return Ksef2Port(environment=KsefEnvironment.TEST)


def test_both_implementations_are_the_port(port: KsefPort) -> None:
    assert isinstance(port, KsefPort)


def test_the_credential_the_port_takes_is_a_stored_token() -> None:
    # GH-115: the token used to be unwrapped at the server boundary and travel
    # as a bare string through seven frames, so a network failure anywhere in
    # between built a traceback carrying the secret in its locals.
    assert isinstance(TOKEN, Credential)


def test_a_bare_string_is_not_a_credential() -> None:
    # The protection is only worth what the type refuses. A `str` satisfying the
    # port's signature would let the old shape back in unnoticed.
    assert not isinstance("aaaabbbbccccdddd", Credential)


def test_the_credential_keeps_the_secret_out_of_what_a_traceback_prints() -> None:
    assert TOKEN.value not in repr(TOKEN)


def test_both_hand_out_something_that_is_a_session(port: KsefPort) -> None:
    with port.session(nip=NIP, token=TOKEN) as session:
        assert isinstance(session, KsefSession)


@pytest.fixture
def readers(tmp_path: Path) -> MeteredPeriods:
    return MeteredPeriods(
        cache=PeriodCache(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            root=tmp_path / "cache",
        ),
        allowance=an_allowance(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path),
    )


def test_the_same_service_greets_the_subject_either_way(
    port: KsefPort, readers: MeteredPeriods
) -> None:
    checked = check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)

    assert checked.subject_name == "Moja Firma sp. z o.o."


def test_the_same_service_returns_the_same_invoices_either_way(
    port: KsefPort, readers: MeteredPeriods
) -> None:
    checked = check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)

    assert [str(invoice.ksef_number) for invoice in checked.invoices] == [
        "1234567890-20260901-0100AB12CD01-56",
        "1234567890-20260901-0100AB12CD02-56",
    ]


def test_the_same_allowances_read_back_either_way(port: KsefPort) -> None:
    with port.session(nip=NIP, token=TOKEN) as session:
        limits = session.read_limits()

    assert limits.rates.metadata_queries.per_hour == 20


def test_an_export_is_scheduled_and_polled_either_way(port: KsefPort) -> None:
    window = Period.for_synchronisation(
        since=datetime(2026, 8, 1, tzinfo=UTC),
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )

    with port.session(nip=NIP, token=TOKEN) as session:
        handle = session.start_export(period=window, subject_role=SubjectRole.BUYER)
        status = session.check_export(handle=handle)

    assert (status.state, status.parts[0].ordinal) == (ExportState.READY, 1)


def test_a_part_comes_back_as_bytes_either_way(port: KsefPort) -> None:
    window = Period.for_synchronisation(
        since=datetime(2026, 8, 1, tzinfo=UTC),
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )

    with port.session(nip=NIP, token=TOKEN) as session:
        handle = session.start_export(period=window, subject_role=SubjectRole.BUYER)
        part = session.check_export(handle=handle).parts[0]
        session.transport = _mock_storage()
        fetched = session.fetch_part(handle=handle, part=part)

    assert fetched == b"\x00encrypted"


def _mock_storage() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"\x00encrypted"))
    )


def imported_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    "module",
    sorted(
        path.name
        for path in (Path(ksef_mcp.__file__).parent / "ksef_port").glob("*.py")
        if path.name != ALLOWED_TO_SEE_THE_SDK
    ),
)
def test_only_the_adapter_is_allowed_to_see_the_sdk(module: str) -> None:
    # "Tools do not see SDK types" is checkable, so it is checked rather than
    # trusted: one module imports `ksef2`, and swapping the client stays a
    # change to that module alone.
    imported = imported_modules(PORT_PACKAGE / module)

    assert not any(name.split(".")[0] == "ksef2" for name in imported)
