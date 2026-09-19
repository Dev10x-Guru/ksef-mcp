import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest
from mcp import Client
from mcp.types import CallToolResult, ListToolsResult

from ksef_mcp import config, token_store
from ksef_mcp.audit import ARCHIVE_BASIS, PDF_FORMAT, AuditEntry, AuditTrail, Disclosure
from ksef_mcp.config import Configuration, KsefEnvironment
from ksef_mcp.ksef_port import (
    DateType,
    InvoiceDirection,
    KsefNumber,
    KsefRefused,
    MetadataPage,
    Period,
)
from ksef_mcp.listing import (
    LISTING_THRESHOLD,
    CurrencyTotal,
    InvoiceListing,
    Question,
    summarise,
)
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.pdf import (
    GeneratorFailed,
    InvoiceNotArchived,
    InvoiceRenderer,
    NodeUnavailable,
)
from ksef_mcp.preflight import NodeReport
from ksef_mcp.review import REVIEW_WINDOW, InvoiceReview, assess
from ksef_mcp.server import (
    InvoiceListingResult,
    InvoiceReviewResult,
    NotConfigured,
    RenderedInvoiceResult,
    StatementResult,
    SynchronisationResult,
    describe,
    describe_listing,
    export_statement,
    list_invoices,
    main,
    render_invoice,
    review_invoices,
    server,
    synchronise,
    window_criteria,
)
from ksef_mcp.statement import AccountingPeriod, Statement, WorkingDirectoryRefused
from ksef_mcp.synchronisation import DirectionReport, SynchronisationReport, SyncOutcome
from synthetic import synthetic_fa3_invoice, synthetic_metadata


@pytest.fixture
async def listed_tools() -> ListToolsResult:
    async with Client(server, raise_exceptions=True) as client:
        return await client.list_tools()


@pytest.fixture
async def server_info_result() -> CallToolResult:
    async with Client(server, raise_exceptions=True) as client:
        return await client.call_tool("server_info")


@pytest.fixture
def recorded_run_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(server, "run", lambda: calls.append("run"))
    main()
    return calls


NIP = "1234567890"

REACHED = datetime(2026, 9, 10, tzinfo=UTC)

ARCHIVED_NUMBER = "1234567890-20260901-0100AB12CD01-56"

HELD_NUMBER = "1234567890-20260901-0100AB12CD02-56"

ARCHIVE_DIRECTORY = "/dane/subjects/1234567890/test/invoices"

# `ksef_mcp/__init__.py` re-exports the `server` object, which shadows the
# submodule of the same name on the package, so `from ksef_mcp import server`
# hands back the MCPServer instance rather than the module these tests patch.
server_module = sys.modules["ksef_mcp.server"]


class StubSynchroniser:
    """Stands in for the pass itself: this module's job is the tool surface."""

    def __init__(self, *, port: object, store: object, allowance: object) -> None:
        self.port = port
        self.store = store
        self.allowance = allowance

    def run(self, *, nip: str, token: str) -> SynchronisationReport:
        return SynchronisationReport(
            directions=(
                DirectionReport(
                    direction=InvoiceDirection.BUYER,
                    outcome=SyncOutcome.ARCHIVED,
                    detail="Paczka EXP-1 trafiła do archiwum.",
                    invoice_count=7,
                    part_count=1,
                    reached=REACHED,
                    archived=(ARCHIVED_NUMBER,),
                    already_held=(HELD_NUMBER,),
                    archive_directory=ARCHIVE_DIRECTORY,
                ),
                DirectionReport(
                    direction=InvoiceDirection.THIRD_SUBJECT,
                    outcome=SyncOutcome.NOT_DUE,
                    detail="Za wcześnie na kolejny eksport dla tego typu podmiotu.",
                ),
            ),
            pending_exports=("EXP-1",),
            state_path="/dane/synchronisation.json",
        )


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Configuration:
    configuration = Configuration(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        keyring_backend="keyring.backends.SecretService.Keyring",
        invoice_directory=tmp_path,
    )
    monkeypatch.setattr(config, "load_configuration", lambda: configuration)
    monkeypatch.setattr(server_module, "Synchroniser", StubSynchroniser)
    monkeypatch.setattr(server_module, "SyncStore", lambda **kwargs: kwargs)
    return configuration


@pytest.fixture
def with_a_token(monkeypatch: pytest.MonkeyPatch, configured: Configuration) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda *, nip: token_store.StoredToken(
            value="tajny-token", source=token_store.TokenSource.KEYRING
        ),
    )


@pytest.fixture
def synchronised(with_a_token: None) -> SynchronisationResult:
    return synchronise()


@pytest.mark.anyio
async def test_every_tool_is_registered(listed_tools: ListToolsResult) -> None:
    assert [tool.name for tool in listed_tools.tools] == [
        "server_info",
        "synchronise_invoices",
        "list_recent_invoices",
        "export_period_statement",
        "review_new_invoices",
        "render_invoice_pdf",
    ]


@pytest.mark.anyio
async def test_synchronisation_takes_no_arguments_from_the_caller(
    listed_tools: ListToolsResult,
) -> None:
    # D-020 is a hard criterion: an agent driving the window or the page size
    # spends a twenty-per-hour allowance in two minutes.
    tool = next(tool for tool in listed_tools.tools if tool.name == "synchronise_invoices")

    assert tool.input_schema.get("properties", {}) == {}


def test_synchronisation_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        synchronise()


async def failing_call(tool: str, arguments: dict[str, str] | None = None) -> CallToolResult:
    async with Client(server) as client:
        return await client.call_tool(tool, arguments)


@pytest.fixture
async def refused_synchronisation(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def refuse() -> SynchronisationResult:
        raise KsefRefused("KSeF call failed: Invalid response payload")

    monkeypatch.setattr(server_module, "synchronise", refuse)
    return await failing_call("synchronise_invoices")


@pytest.fixture
async def unconfigured_listing(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def unconfigured() -> InvoiceListingResult:
        raise NotConfigured("no configuration file")

    monkeypatch.setattr(server_module, "list_invoices", unconfigured)
    return await failing_call("list_recent_invoices")


@pytest.fixture
async def crashed_review(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def crash() -> InvoiceReviewResult:
        raise ZeroDivisionError("a path fragment nobody vetted")

    monkeypatch.setattr(server_module, "review_invoices", crash)
    return await failing_call("review_new_invoices")


@pytest.mark.anyio
async def test_a_port_failure_reaches_the_caller_with_its_reason(
    refused_synchronisation: CallToolResult,
) -> None:
    # Not a bare `Error executing tool synchronise_invoices` with the reason
    # stranded in a stderr nobody reads — that is how GH-76 looked like a dead
    # server while `verify` was passing.
    assert "Invalid response payload" in str(refused_synchronisation.content)


@pytest.mark.anyio
async def test_a_port_failure_is_reported_as_a_failure(
    refused_synchronisation: CallToolResult,
) -> None:
    assert refused_synchronisation.is_error


@pytest.mark.anyio
async def test_a_missing_configuration_names_the_command_that_fixes_it(
    unconfigured_listing: CallToolResult,
) -> None:
    assert "ksef-mcp onboarding" in str(unconfigured_listing.content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            InvoiceNotArchived("Tej faktury nie ma w archiwum. Uruchom najpierw synchronizację."),
            "Uruchom najpierw synchronizację",
        ),
        (
            WorkingDirectoryRefused("Katalog roboczy leży wewnątrz magazynu wewnętrznego."),
            "magazynu wewnętrznego",
        ),
        (NodeUnavailable("Wizualizacja wymaga Node w wersji 20 lub nowszej."), "Node"),
        (
            GeneratorFailed("Generator odrzucił dokument: Unknown XML Version: undefined"),
            "Unknown XML Version",
        ),
    ],
)
async def test_a_render_refusal_travels_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: str,
) -> None:
    """GH-84: this was the one tool outside `reported`.

    Every refusal its own docstring promises — the invoice not synchronised yet,
    the working directory declined, Node missing, the generator refusing the
    document — reached the caller as a bare `Error executing tool`.
    """

    def refuse(*, ksef_number: str, working_directory: str | None) -> RenderedInvoiceResult:
        raise failure

    monkeypatch.setattr(server_module, "render_invoice", refuse)

    answered = await failing_call("render_invoice_pdf", {"ksef_number": RENDER_NUMBER})

    assert expected in str(answered.content)


@pytest.mark.anyio
async def test_a_genuine_crash_keeps_its_text_off_the_wire(
    crashed_review: CallToolResult,
) -> None:
    # The boundary is narrow on purpose: only failures written for a reader
    # travel. An unforeseen crash may carry anything — a path, a fragment of a
    # payload — so it keeps the SDK's generic message.
    assert "a path fragment nobody vetted" not in str(crashed_review.content)


def test_synchronisation_refuses_without_a_token(
    monkeypatch: pytest.MonkeyPatch, configured: Configuration
) -> None:
    monkeypatch.setattr(token_store, "read_token", lambda *, nip: None)

    with pytest.raises(NotConfigured, match="token set"):
        synchronise()


def test_the_answer_names_the_environment_it_spoke_to(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.environment == "test"


def test_the_answer_reports_every_subject_type(synchronised: SynchronisationResult) -> None:
    assert [one.subject_type for one in synchronised.subject_types] == [
        "buyer",
        "third_subject",
    ]


def test_the_answer_says_how_far_each_subject_type_reached(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_types[0].synchronised_up_to == REACHED.isoformat()


def test_a_subject_type_that_never_ran_reports_no_position(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_types[1].synchronised_up_to is None


def test_the_answer_counts_what_the_package_carries(
    synchronised: SynchronisationResult,
) -> None:
    assert (
        synchronised.subject_types[0].invoice_count,
        synchronised.subject_types[0].part_count,
    ) == (7, 1)


def test_the_answer_says_where_the_invoices_landed(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_types[0].archive_directory == ARCHIVE_DIRECTORY


def test_the_answer_names_the_numbers_it_stored(synchronised: SynchronisationResult) -> None:
    assert synchronised.subject_types[0].archived == [ARCHIVED_NUMBER]


def test_the_answer_separates_what_was_already_held(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_types[0].already_held == [HELD_NUMBER]


def test_a_subject_type_that_stored_nothing_reports_no_directory(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_types[1].archive_directory is None


@pytest.mark.anyio
async def test_the_tool_answers_with_paths_and_numbers_but_no_invoice(
    with_a_token: None,
) -> None:
    # An FA(2)/FA(3) body carries the counterparty's personal data, so the tool
    # names the file and never opens it (D-011).
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("synchronise_invoices")

    assert "Faktura" not in str(called.structured_content)


def test_the_answer_points_at_the_packages_left_to_decrypt(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.pending_exports == ["EXP-1"]


def test_the_answer_points_at_the_record_on_disk(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.state_file == "/dane/synchronisation.json"


@pytest.mark.anyio
async def test_the_tool_hands_back_the_pass_it_ran(with_a_token: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("synchronise_invoices")

    assert called.structured_content["pending_exports"] == ["EXP-1"]


def test_the_description_survives_an_empty_pass() -> None:
    described = describe(
        SynchronisationReport(directions=(), pending_exports=(), state_path="/dane/x.json"),
        environment=KsefEnvironment.DEMO,
    )

    assert described.subject_types == []


@pytest.mark.anyio
async def test_server_info_reports_package_metadata(
    server_info_result: CallToolResult,
) -> None:
    assert server_info_result.structured_content == {
        "name": SERVER_NAME,
        "version": VERSION,
    }


@pytest.mark.anyio
async def test_server_info_call_succeeds(server_info_result: CallToolResult) -> None:
    assert server_info_result.is_error is not True


def test_main_starts_the_server(recorded_run_calls: list[str]) -> None:
    assert recorded_run_calls == ["run"]


LISTING_PERIOD = Period(
    date_from=datetime(2026, 8, 15, 7, tzinfo=UTC),
    date_to=datetime(2026, 9, 14, 7, tzinfo=UTC),
    date_type=DateType.ISSUE,
)


def listed_question(direction: InvoiceDirection) -> Question:
    return Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        direction=direction,
        period=LISTING_PERIOD,
    )


class StubLister:
    """Stands in for the pass over the subject types: this module's job is the surface."""

    def __init__(self, *, port: object, cache: object, allowance: object) -> None:
        self.port = port
        self.cache = cache
        self.allowance = allowance

    def run(self, *, nip: str, token: str) -> InvoiceListing:
        return InvoiceListing(
            nip=nip,
            environment=KsefEnvironment.TEST,
            threshold=LISTING_THRESHOLD,
            period=LISTING_PERIOD,
            directions=(
                summarise(
                    question=listed_question(InvoiceDirection.BUYER),
                    page=MetadataPage(
                        invoices=(synthetic_metadata(1),),
                        has_more=False,
                        truncated=False,
                        hwm_date=None,
                    ),
                    queried_at=REACHED,
                    from_cache=True,
                ),
                summarise(
                    question=listed_question(InvoiceDirection.SELLER),
                    page=MetadataPage(invoices=(), has_more=False, truncated=False, hwm_date=None),
                    queried_at=REACHED,
                ),
            ),
        )


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> InvoiceListingResult:
    monkeypatch.setattr(server_module, "InvoiceLister", StubLister)
    monkeypatch.setattr(server_module, "PeriodCache", lambda **kwargs: kwargs)
    return list_invoices()


def test_the_listing_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        list_invoices()


def test_the_listing_names_the_subject_it_acted_as(listing: InvoiceListingResult) -> None:
    assert (listing.nip, listing.environment) == (NIP, "test")


def test_the_listing_states_the_window_once(listing: InvoiceListingResult) -> None:
    assert (listing.period_from, listing.period_to) == (
        LISTING_PERIOD.date_from.isoformat(),
        LISTING_PERIOD.date_to.isoformat(),
    )


def test_the_listing_states_the_threshold_it_applied(listing: InvoiceListingResult) -> None:
    assert listing.threshold == 50


def test_the_listing_reports_every_subject_type_it_asked_about(
    listing: InvoiceListingResult,
) -> None:
    assert [one.subject_type for one in listing.subject_types] == ["buyer", "seller"]


def test_a_listed_subject_type_carries_the_eight_columns(listing: InvoiceListingResult) -> None:
    row = listing.subject_types[0].invoices[0]

    assert (row.ksef_number, row.seller_invoice_number, row.currency) == (
        str(synthetic_metadata(1).ksef_number),
        "FV/2026/09/001",
        "PLN",
    )


def test_a_listed_subject_type_reports_its_gross_total(listing: InvoiceListingResult) -> None:
    assert listing.subject_types[0].gross_totals[0].gross == Decimal("1230.00")


def test_a_listed_subject_type_says_whether_disk_answered(
    listing: InvoiceListingResult,
) -> None:
    assert listing.subject_types[0].from_cache is True


def test_a_listed_subject_type_says_when_it_was_asked(listing: InvoiceListingResult) -> None:
    assert listing.subject_types[0].queried_at == REACHED.isoformat()


def test_an_empty_subject_type_is_reported_as_such(listing: InvoiceListingResult) -> None:
    assert (listing.subject_types[1].outcome, listing.subject_types[1].invoices) == ("empty", [])


def test_an_empty_subject_type_restates_the_question(listing: InvoiceListingResult) -> None:
    assert f"NIP {NIP}" in listing.subject_types[1].message


def test_a_described_window_names_the_end_it_asked_about() -> None:
    # Od GH-84 nie ma okna bez końca, więc opis zawsze ma co podać.
    described = describe_listing(
        InvoiceListing(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            threshold=LISTING_THRESHOLD,
            period=Period(
                date_from=datetime(2026, 9, 1, tzinfo=UTC),
                date_to=datetime(2026, 9, 14, tzinfo=UTC),
                date_type=DateType.PERMANENT_STORAGE,
            ),
            directions=(),
        )
    )

    assert described.period_to == "2026-09-14T00:00:00+00:00"


@pytest.mark.anyio
async def test_the_listing_tool_takes_no_arguments_from_the_caller(
    listed_tools: ListToolsResult,
) -> None:
    # D-020 again: a window driven by an agent empties a twenty-per-hour
    # allowance in minutes.
    tool = next(tool for tool in listed_tools.tools if tool.name == "list_recent_invoices")

    assert tool.input_schema.get("properties", {}) == {}


@pytest.mark.anyio
async def test_the_listing_tool_answers_with_metadata_but_no_invoice(
    monkeypatch: pytest.MonkeyPatch, with_a_token: None
) -> None:
    monkeypatch.setattr(server_module, "InvoiceLister", StubLister)
    monkeypatch.setattr(server_module, "PeriodCache", lambda **kwargs: kwargs)

    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("list_recent_invoices")

    assert "<Faktura" not in str(called.structured_content)


@pytest.mark.anyio
async def test_the_listing_tool_asks_for_no_confirmation(
    monkeypatch: pytest.MonkeyPatch, with_a_token: None
) -> None:
    # Bramka zgody przy odczycie uczy klikać „tak" bez patrzenia i psuje moment,
    # w którym pytanie naprawdę coś znaczy (D-011).
    monkeypatch.setattr(server_module, "InvoiceLister", StubLister)
    monkeypatch.setattr(server_module, "PeriodCache", lambda **kwargs: kwargs)

    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("list_recent_invoices")

    assert called.is_error is not True


STATEMENT_PATH = "/robocze/1234567890/zestawienie-2026-08-1234567890.csv"


class StubComposer:
    """Stands in for composing the month: this module's job is the tool surface."""

    directories: ClassVar[list[Path]] = []

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
    ) -> Statement:
        type(self).directories.append(directory)
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


@pytest.fixture
def composing(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> None:
    StubComposer.directories = []
    monkeypatch.setattr(server_module, "StatementComposer", StubComposer)
    monkeypatch.setattr(server_module, "PeriodCache", lambda **kwargs: kwargs)
    monkeypatch.setattr(server_module, "InvoiceArchive", lambda **kwargs: kwargs)


@pytest.fixture
def exported(composing: None) -> StatementResult:
    return export_statement(period="2026-08", working_directory=None)


def test_the_statement_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        export_statement(period="2026-08", working_directory=None)


def test_the_statement_lands_in_the_configured_working_directory(
    exported: StatementResult, configured: Configuration
) -> None:
    assert StubComposer.directories == [configured.invoice_directory]


def test_a_caller_may_declare_the_directory_for_one_call(composing: None, tmp_path: Path) -> None:
    export_statement(period="2026-08", working_directory=str(tmp_path / "gdzie indziej"))

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


REVIEW_ENDS = datetime(2026, 9, 14, 7, tzinfo=UTC)

# Początek wyprowadzony z REVIEW_WINDOW, nie przepisany liczbą (GH-84).
REVIEW_PERIOD = Period(
    date_from=REVIEW_ENDS - REVIEW_WINDOW,
    date_to=REVIEW_ENDS,
    date_type=DateType.INVOICING,
)

LEDGER_PATH = "/dane/subjects/1234567890/test/review.json"

# Numer nadany w lipcu, wykryty we wrześniu — przypadek z badania [D-025].
LATE_NUMBER = "1234567890-20260707-0100AB12CD77-56"


def reviewed_question(direction: InvoiceDirection) -> Question:
    return Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        direction=direction,
        period=REVIEW_PERIOD,
    )


class StubReviewer:
    """Stands in for the comparison itself: this module's job is the tool surface."""

    def __init__(
        self,
        *,
        port: object,
        cache: object,
        store: object,
        allowance: object,
    ) -> None:
        self.port = port
        self.cache = cache
        self.store = store
        self.allowance = allowance

    def run(self, *, nip: str, token: str) -> InvoiceReview:
        late = replace(synthetic_metadata(1), ksef_number=KsefNumber(LATE_NUMBER))
        return InvoiceReview(
            nip=nip,
            environment=KsefEnvironment.TEST,
            threshold=LISTING_THRESHOLD,
            period=REVIEW_PERIOD,
            ledger_path=LEDGER_PATH,
            directions=(
                assess(
                    question=reviewed_question(InvoiceDirection.BUYER),
                    invoices=(late,),
                    reviewed=frozenset(),
                    complete=True,
                    budget_bound=False,
                    moment=REVIEW_PERIOD.date_to,
                    queried_at=REACHED,
                    from_cache=True,
                ),
                assess(
                    question=reviewed_question(InvoiceDirection.SELLER),
                    invoices=(),
                    reviewed=frozenset(),
                    complete=True,
                    budget_bound=False,
                    moment=REVIEW_PERIOD.date_to,
                    queried_at=REACHED,
                    from_cache=False,
                ),
            ),
        )


@pytest.fixture
def reviewing(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> None:
    monkeypatch.setattr(server_module, "InvoiceReviewer", StubReviewer)
    monkeypatch.setattr(server_module, "PeriodCache", lambda **kwargs: kwargs)
    monkeypatch.setattr(server_module, "ReviewStore", lambda **kwargs: kwargs)


@pytest.fixture
def review(reviewing: None) -> InvoiceReviewResult:
    return review_invoices()


def test_the_review_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        review_invoices()


def test_the_review_names_the_subject_it_acted_as(review: InvoiceReviewResult) -> None:
    assert (review.nip, review.environment, review.threshold) == (NIP, "test", 50)


def test_the_review_states_the_window_once(review: InvoiceReviewResult) -> None:
    assert (review.period_from, review.period_to) == (
        REVIEW_PERIOD.date_from.isoformat(),
        REVIEW_PERIOD.date_to.isoformat(),
    )


def test_the_review_points_at_the_ledger_that_survives_the_session(
    review: InvoiceReviewResult,
) -> None:
    assert review.ledger_file == LEDGER_PATH


def test_a_new_invoice_carries_the_day_ksef_gave_it_its_number(
    review: InvoiceReviewResult,
) -> None:
    # Data otrzymania, nie data pobrania i nie data wystawienia sprzedawcy.
    row = review.subject_types[0].new_invoices[0]

    assert (row.received_on, row.issue_date) == ("2026-07-07", "2026-09-01")


def test_an_invoice_from_an_earlier_month_is_counted_as_a_signal(
    review: InvoiceReviewResult,
) -> None:
    assert review.subject_types[0].earlier_month_count == 1


def test_a_reported_invoice_is_recorded_as_shown(review: InvoiceReviewResult) -> None:
    assert review.subject_types[0].marked_as_reviewed is True


def test_a_subject_type_with_nothing_new_says_so(review: InvoiceReviewResult) -> None:
    assert (review.subject_types[1].outcome, review.subject_types[1].new_count) == (
        "nothing_new",
        0,
    )


def test_the_review_says_whether_disk_answered(review: InvoiceReviewResult) -> None:
    assert [one.from_cache for one in review.subject_types] == [True, False]


def test_the_review_says_when_it_was_asked(review: InvoiceReviewResult) -> None:
    assert review.subject_types[0].queried_at == REACHED.isoformat()


def test_the_review_carries_the_gross_total_of_what_is_new(
    review: InvoiceReviewResult,
) -> None:
    assert review.subject_types[0].gross_totals[0].gross == Decimal("1230.00")


@pytest.mark.anyio
async def test_the_review_tool_takes_no_arguments_from_the_caller(
    listed_tools: ListToolsResult,
) -> None:
    tool = next(tool for tool in listed_tools.tools if tool.name == "review_new_invoices")

    assert tool.input_schema.get("properties", {}) == {}


@pytest.mark.anyio
async def test_the_review_tool_answers_with_metadata_but_no_invoice(reviewing: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("review_new_invoices")

    assert "<Faktura" not in str(called.structured_content)


@pytest.mark.anyio
async def test_the_review_tool_hands_back_what_is_new(reviewing: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("review_new_invoices")

    assert called.structured_content["subject_types"][0]["new_count"] == 1


# Ślad audytowy (#45). W sporze o wyciek to jedyny dowód, więc każde narzędzie
# odczytu zostawia wpis — niezależnie od tego, że odczyt nie ma bramki zgody.


@pytest.fixture
def trail(subject_data_root: Path) -> AuditTrail:
    return AuditTrail(nip=NIP, environment=KsefEnvironment.TEST, root=subject_data_root)


def recorded_reads(trail: AuditTrail) -> tuple[AuditEntry, ...]:
    return trail.entries()


def test_a_synchronisation_records_what_it_stored(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    stored = recorded_reads(trail)[0]

    assert (stored.operation, stored.disclosure, stored.ksef_numbers) == (
        "synchronise_invoices",
        Disclosure.DISK,
        (ARCHIVED_NUMBER,),
    )


def test_a_synchronisation_records_a_deduplication_skip_as_a_skip(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    # Ślad złożony wyłącznie z zapisów czytałby się tak, jakby tej faktury nigdy
    # nie dotknięto — a była widziana i rozpoznana jako już posiadana.
    skipped = recorded_reads(trail)[1]

    assert (skipped.disclosure, skipped.ksef_numbers) == (
        Disclosure.DEDUPLICATION_SKIP,
        (HELD_NUMBER,),
    )


def test_a_synchronisation_records_the_subject_and_the_footing(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    stored = recorded_reads(trail)[0]

    assert (stored.authorisation.nip, stored.authorisation.basis) == (NIP, "ksef_token:keyring")


def test_a_synchronisation_records_where_the_invoices_landed(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].output_path == ARCHIVE_DIRECTORY


def test_a_subject_type_that_touched_nothing_leaves_no_entry(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    assert len(recorded_reads(trail)) == 2


def test_no_token_value_reaches_the_trail(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    assert "tajny-token" not in trail.path.read_text(encoding="utf-8")


def test_a_listing_records_that_the_model_was_shown_the_rows(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    # Osobne zdarzenie niż zapis na dysk (D-011): „czat zobaczył" to nie to samo
    # co „plik powstał".
    shown = recorded_reads(trail)[0]

    assert (shown.operation, shown.disclosure, shown.output_path) == (
        "list_recent_invoices",
        Disclosure.MODEL_CONTEXT,
        None,
    )


def test_a_listing_records_the_window_it_asked_about(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].criteria == (
        f"issue_date {LISTING_PERIOD.date_from.isoformat()}..{LISTING_PERIOD.date_to.isoformat()}"
    )


def test_a_listing_records_the_numbers_that_reached_the_answer(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].ksef_numbers == (str(synthetic_metadata(1).ksef_number),)


def test_a_listing_records_a_subject_type_that_returned_nothing(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    # Pytanie padło, więc zakres, o który zapytano, jest częścią śladu.
    empty = recorded_reads(trail)[1]

    assert (empty.subject_role, empty.document_count, empty.ksef_numbers) == ("seller", 0, ())


def test_the_recorded_window_states_both_ends() -> None:
    # Ślad audytowy ma pozwolić odtworzyć zakres pytania, a od GH-84 zakres
    # zawsze ma oba końce — nie ma już wpisu kończącego się na „open".
    asked = Period(
        date_from=datetime(2026, 9, 1, tzinfo=UTC),
        date_to=datetime(2026, 9, 14, tzinfo=UTC),
        date_type=DateType.PERMANENT_STORAGE,
    )

    assert window_criteria(asked).endswith("2026-09-01T00:00:00+00:00..2026-09-14T00:00:00+00:00")


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


def test_a_review_records_what_the_reader_was_shown(
    review: InvoiceReviewResult, trail: AuditTrail
) -> None:
    shown = recorded_reads(trail)[0]

    assert (shown.operation, shown.disclosure, shown.ksef_numbers) == (
        "review_new_invoices",
        Disclosure.MODEL_CONTEXT,
        (LATE_NUMBER,),
    )


def test_a_review_records_the_window_and_the_count(
    review: InvoiceReviewResult, trail: AuditTrail
) -> None:
    assert (recorded_reads(trail)[0].criteria, recorded_reads(trail)[0].document_count) == (
        f"invoicing_date {REVIEW_PERIOD.date_from.isoformat()}"
        f"..{REVIEW_PERIOD.date_to.isoformat()}",
        1,
    )


def test_a_review_records_a_subject_type_with_nothing_new(
    review: InvoiceReviewResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[1].document_count == 0


# Renderowanie PDF (#42). Narzędzie nie sięga do KSeF — otwiera to, co archiwum
# już trzyma — więc jego testy nie potrzebują tokenu ani atrapy portu.

RENDER_NUMBER = "1234567890-20260817-0100AB12CD01-56"


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

    monkeypatch.setattr(server_module, "InvoiceArchive", StubArchive)
    monkeypatch.setattr(
        server_module,
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
    return render_invoice(ksef_number=RENDER_NUMBER, working_directory=None)


def test_rendering_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        render_invoice(ksef_number=RENDER_NUMBER, working_directory=None)


def test_the_answer_points_at_the_document_it_wrote(rendered: RenderedInvoiceResult) -> None:
    assert Path(rendered.path).is_file()


def test_the_answer_names_the_invoice_it_opened(rendered: RenderedInvoiceResult) -> None:
    assert rendered.ksef_number == RENDER_NUMBER


def test_the_answer_names_the_generator_build(rendered: RenderedInvoiceResult) -> None:
    assert rendered.generator_version == "1.1.39"


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

    result = render_invoice(ksef_number=RENDER_NUMBER, working_directory=str(elsewhere))

    assert Path(result.path).parent == elsewhere


def test_a_cloud_synced_directory_is_named_before_the_pdf_leaves(
    rendering: None,
    tmp_path: Path,
) -> None:
    result = render_invoice(
        ksef_number=RENDER_NUMBER,
        working_directory=str(tmp_path / "OneDrive" / "faktury"),
    )

    assert "onedrive" in result.warnings[0]


def test_a_private_directory_leaves_the_render_answer_without_caveats(
    rendered: RenderedInvoiceResult,
) -> None:
    assert rendered.warnings == []


def test_an_unsynchronised_invoice_is_refused_rather_than_fetched(rendering: None) -> None:
    with pytest.raises(InvoiceNotArchived, match="Uruchom najpierw"):
        render_invoice(
            ksef_number="1234567890-20260817-0100AB12CD99-56",
            working_directory=None,
        )


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
    assert recorded_reads(trail)[0].authorisation.basis == ARCHIVE_BASIS


@pytest.mark.anyio
async def test_the_render_tool_answers_with_a_path_but_no_invoice(rendering: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("render_invoice_pdf", {"ksef_number": RENDER_NUMBER})

    assert called.structured_content["path"].endswith(f"{RENDER_NUMBER}.pdf")
