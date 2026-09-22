from types import ModuleType

import pytest
from mcp.types import CallToolResult, ListToolsResult

from ksef_mcp import config
from ksef_mcp.errors import KsefMcpError
from ksef_mcp.invoices.statement import WorkingDirectoryRefused
from ksef_mcp.ksef_port import KsefPortError, KsefRefused
from ksef_mcp.rendering.pdf import GeneratorFailed, InvoiceNotArchived, NodeUnavailable
from ksef_mcp.server import errors as server_errors
from ksef_mcp.server import (
    tools_listing,
    tools_rendering,
    tools_review,
    tools_statement,
    tools_synchronisation,
)
from ksef_mcp.server.app import Journal
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.server.results import (
    InvoiceListingResult,
    InvoiceReviewResult,
    RenderedInvoiceResult,
    SynchronisationResult,
)
from ksef_mcp.storage import token_store
from tests.server.conftest import NIP, RENDER_NUMBER, failing_call


@pytest.mark.anyio
async def test_every_tool_is_registered(listed_tools: ListToolsResult) -> None:
    # Sorted, because the order tools appear in is now the order the package
    # imports their modules — an alphabetical artifact of the split (#133), not
    # a promise to the client. What the client is owed is the set.
    assert sorted(tool.name for tool in listed_tools.tools) == [
        "export_period_statement",
        "list_recent_invoices",
        "render_invoice_pdf",
        "review_new_invoices",
        "server_info",
        "synchronise_invoices",
    ]


@pytest.fixture
async def refused_synchronisation(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def refuse(*, journal: Journal) -> SynchronisationResult:
        raise KsefRefused("KSeF call failed: Invalid response payload")

    monkeypatch.setattr(tools_synchronisation, "synchronise", refuse)
    return await failing_call("synchronise_invoices")


@pytest.fixture
async def unconfigured_listing(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def unconfigured(*, journal: Journal) -> InvoiceListingResult:
        raise NotConfigured("no configuration file")

    monkeypatch.setattr(tools_listing, "list_invoices", unconfigured)
    return await failing_call("list_recent_invoices")


@pytest.fixture
async def damaged_configuration(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def refuse(*, journal: Journal) -> SynchronisationResult:
        raise config.ConfigurationUnreadable(
            "/dane/configuration.json is not readable JSON — an interrupted "
            "write leaves the file truncated. Run `ksef-mcp onboarding` to "
            "write it again."
        )

    monkeypatch.setattr(tools_synchronisation, "synchronise", refuse)
    return await failing_call("synchronise_invoices")


@pytest.fixture
async def crashed_review(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def crash(*, journal: Journal) -> InvoiceReviewResult:
        raise ZeroDivisionError("a path fragment nobody vetted")

    monkeypatch.setattr(tools_review, "review_invoices", crash)
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
async def test_a_damaged_configuration_says_the_file_is_damaged(
    damaged_configuration: CallToolResult,
) -> None:
    # GH-119: the agent used to see a bare `Error executing tool
    # synchronise_invoices`, which reads as a broken server rather than as one
    # file to rewrite. Same failure mode GH-76 and GH-84 already fixed
    # elsewhere.
    assert "not readable JSON" in str(damaged_configuration.content)


@pytest.mark.anyio
async def test_a_damaged_configuration_names_the_file_and_the_remedy(
    damaged_configuration: CallToolResult,
) -> None:
    said = str(damaged_configuration.content)

    assert "/dane/configuration.json" in said
    assert "ksef-mcp onboarding" in said


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

    def refuse(
        *,
        ksef_number: str,
        working_directory: str | None,
        journal: Journal,
    ) -> RenderedInvoiceResult:
        raise failure

    monkeypatch.setattr(tools_rendering, "render_invoice", refuse)

    answered = await failing_call("render_invoice_pdf", {"ksef_number": RENDER_NUMBER})

    assert expected in str(answered.content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "module", "entry_point", "arguments"),
    [
        ("synchronise_invoices", tools_synchronisation, "synchronise", {}),
        ("list_recent_invoices", tools_listing, "list_invoices", {}),
        ("export_period_statement", tools_statement, "export_statement", {"period": "2026-08"}),
        ("review_new_invoices", tools_review, "review_invoices", {}),
    ],
)
async def test_a_keyring_asleep_with_the_laptop_says_so_from_every_tool(
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    module: ModuleType,
    entry_point: str,
    arguments: dict[str, str],
) -> None:
    """GH-167: four of five tools read a token, and none reached this sentence.

    A keyring collection locks itself when the machine suspends, so this is the
    daily working cycle rather than an edge case. The explaining message was
    already written and the CLI already showed it; the server threw it away.
    """

    def locked(**arguments: object) -> None:
        raise token_store.TokenStoreLocked(token_store.LOCKED_MESSAGE)

    monkeypatch.setattr(module, entry_point, locked)

    answered = await failing_call(tool, arguments)

    assert "Unlock the collection" in str(answered.content)


def test_every_refusal_of_this_application_shares_one_root() -> None:
    # The tuple used to be a list of names, and the list went stale (GH-167).
    assert server_errors.REFUSALS == (KsefPortError, KsefMcpError)


@pytest.mark.anyio
async def test_a_genuine_crash_keeps_its_text_off_the_wire(
    crashed_review: CallToolResult,
) -> None:
    # The boundary is narrow on purpose: only failures written for a reader
    # travel. An unforeseen crash may carry anything — a path, a fragment of a
    # payload — so it keeps the SDK's generic message.
    assert "a path fragment nobody vetted" not in str(crashed_review.content)


# Audit journal completeness (#136). Nothing enforced `trail.record`: a new
# tool could fetch data, answer, and leave no trail — and the whole value of
# the journal rests on the entry always being there (D-011).


@pytest.fixture
async def answered_without_the_journal(monkeypatch: pytest.MonkeyPatch) -> CallToolResult:
    def forgetful(*, journal: Journal) -> InvoiceListingResult:
        return InvoiceListingResult(
            nip=NIP,
            environment="test",
            message="Faktury w oknie: 0.",
            threshold=50,
            period_from="2026-09-01T00:00:00+00:00",
            period_to="2026-09-14T00:00:00+00:00",
            subject_roles=[],
        )

    monkeypatch.setattr(tools_listing, "list_invoices", forgetful)
    return await failing_call("list_recent_invoices")


@pytest.mark.anyio
async def test_a_tool_that_skips_the_audit_write_is_refused(
    answered_without_the_journal: CallToolResult,
) -> None:
    assert answered_without_the_journal.is_error


@pytest.mark.anyio
async def test_a_skipped_audit_write_says_which_record_is_missing(
    answered_without_the_journal: CallToolResult,
) -> None:
    # The operation name and a sentence about the journal — never the KSeF
    # numbers this answer was supposed to cover.
    assert "dziennika audytu" in str(answered_without_the_journal.content)
