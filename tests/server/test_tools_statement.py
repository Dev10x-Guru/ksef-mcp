from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest
from mcp import Client
from mcp.types import ListToolsResult

from ksef_mcp import config
from ksef_mcp.config import Configuration
from ksef_mcp.invoices.listing import CurrencyTotal
from ksef_mcp.invoices.statement import AccountingPeriod, Statement
from ksef_mcp.ksef_port import KsefEnvironment
from ksef_mcp.server import context, server, tools_statement
from ksef_mcp.server.app import Journal
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.server.results import StatementResult
from ksef_mcp.server.tools_statement import export_statement
from ksef_mcp.storage.audit import AuditedOperation, AuditTrail, Disclosure
from tests.server.conftest import NIP, REACHED, recorded_reads

STATEMENT_PATH = "/robocze/1234567890/zestawienie-2026-08-1234567890.csv"


class StubComposer:
    """Stands in for composing the month: this module's job is the tool surface."""

    directories: ClassVar[list[Path]] = []
    markers: ClassVar[list[Callable[[Path], str | None]]] = []

    def __init__(
        self,
        *,
        port: object,
        cache: object,
        archive: object,
        allowance: object,
    ) -> None:
        self.port = port
        self.cache = cache
        self.archive = archive
        self.allowance = allowance

    def run(
        self,
        *,
        nip: str,
        token: str,
        period: AccountingPeriod,
        directory: Path,
        cloud_marker_for: Callable[[Path], str | None],
    ) -> Statement:
        type(self).directories.append(directory)
        type(self).markers.append(cloud_marker_for)
        return Statement(
            nip=nip,
            environment=KsefEnvironment.TEST,
            period=period,
            path=STATEMENT_PATH,
            row_count=1,
            gross_totals=(CurrencyTotal(currency="PLN", gross=Decimal("1230.00")),),
            complete=True,
            budget_bound=False,
            from_cache=False,
            queried_at=REACHED,
            warnings=("Katalog roboczy wygląda na synchronizowany do chmury (onedrive).",),
        )


def exported_statement(*, working_directory: str | None = None) -> StatementResult:
    return export_statement(
        period="2026-08",
        working_directory=working_directory,
        journal=Journal(operation=AuditedOperation.STATEMENT),
    )


@pytest.fixture
def composing(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> None:
    StubComposer.directories = []
    StubComposer.markers = []
    monkeypatch.setattr(tools_statement, "StatementComposer", StubComposer)
    monkeypatch.setattr(context, "PeriodCache", lambda **kwargs: kwargs)
    monkeypatch.setattr(context, "InvoiceArchive", lambda **kwargs: kwargs)


@pytest.fixture
def exported(composing: None) -> StatementResult:
    return exported_statement()


def test_the_statement_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        exported_statement()


def test_the_statement_lands_in_the_configured_working_directory(
    exported: StatementResult, configured: Configuration
) -> None:
    assert StubComposer.directories == [configured.working_directory]


def test_a_caller_may_declare_the_directory_for_one_call(composing: None, tmp_path: Path) -> None:
    exported_statement(working_directory=str(tmp_path / "gdzie indziej"))

    assert StubComposer.directories == [tmp_path / "gdzie indziej"]


def test_the_answer_points_at_the_file_it_wrote(exported: StatementResult) -> None:
    assert exported.path == STATEMENT_PATH


def test_the_answer_states_the_period_it_closed(exported: StatementResult) -> None:
    assert exported.period == "2026-08"


def test_the_answer_carries_the_sum_per_currency(exported: StatementResult) -> None:
    assert (exported.gross_totals[0].currency, exported.gross_totals[0].gross) == (
        "PLN",
        Decimal("1230.00"),
    )


def test_the_answer_repeats_the_directory_warning(exported: StatementResult) -> None:
    assert "onedrive" in exported.warnings[0]


def test_the_composer_judges_syncing_by_the_configuration(
    composing: None,
    configured: Configuration,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # GH-252: the tool hands the composer the configuration's own judgement,
    # so a directory acknowledged during onboarding is not warned about again.
    acknowledged = configured.acknowledging(tmp_path / "OneDrive" / "ksef")
    monkeypatch.setattr(config, "load_configuration", lambda: acknowledged)

    exported_statement()

    assert StubComposer.markers[0](tmp_path / "OneDrive" / "ksef") is None


def test_the_answer_says_when_it_was_asked(exported: StatementResult) -> None:
    assert exported.queried_at == REACHED.isoformat()


def test_the_answer_says_whether_disk_answered(exported: StatementResult) -> None:
    assert (exported.from_cache, exported.complete) == (False, True)


def test_the_answer_names_the_subject_it_acted_as(exported: StatementResult) -> None:
    assert (exported.nip, exported.environment, exported.row_count) == (NIP, "test", 1)


@pytest.mark.anyio
async def test_the_statement_tool_takes_the_period_it_is_told(
    listed_tools: ListToolsResult,
) -> None:
    # Unlike the listing, a statement is about a month the person names — and a
    # closed month is answered from disk, so naming it spends nothing.
    tool = next(tool for tool in listed_tools.tools if tool.name == "export_period_statement")

    assert sorted(tool.input_schema.get("properties", {})) == ["period", "working_directory"]


@pytest.mark.anyio
async def test_the_statement_tool_answers_with_a_path_but_no_invoice(composing: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("export_period_statement", {"period": "2026-08"})

    assert called.structured_content["path"] == STATEMENT_PATH


def test_a_statement_records_the_file_it_wrote(
    exported: StatementResult, trail: AuditTrail
) -> None:
    written = recorded_reads(trail)[0]

    assert (written.operation, written.disclosure, written.output_path, written.formats) == (
        "export_period_statement",
        Disclosure.DISK,
        STATEMENT_PATH,
        ("csv",),
    )


def test_a_statement_records_the_month_it_closed(
    exported: StatementResult, trail: AuditTrail
) -> None:
    assert (recorded_reads(trail)[0].criteria, recorded_reads(trail)[0].subject_role) == (
        "2026-08",
        "buyer",
    )


def test_a_statement_records_the_numbers_its_rows_name(
    exported: StatementResult, trail: AuditTrail
) -> None:
    # `StatementResult` ich nie niesie — pięćdziesiąt numerów w oknie czatu to
    # hałas — ale zapis dostępu bez numerów nie odtwarza jego zakresu.
    assert recorded_reads(trail)[0].document_count == 1
