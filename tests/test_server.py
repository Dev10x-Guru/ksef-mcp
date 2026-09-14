import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import CallToolResult, ListToolsResult

from ksef_mcp import config, token_store
from ksef_mcp.config import Configuration, KsefEnvironment
from ksef_mcp.ksef_port import InvoiceDirection
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.server import (
    NotConfigured,
    SynchronisationResult,
    describe,
    main,
    server,
    synchronise,
)
from ksef_mcp.synchronisation import DirectionReport, SynchronisationReport, SyncOutcome


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

# `ksef_mcp/__init__.py` re-exports the `server` object, which shadows the
# submodule of the same name on the package, so `from ksef_mcp import server`
# hands back the MCPServer instance rather than the module these tests patch.
server_module = sys.modules["ksef_mcp.server"]


class StubSynchroniser:
    """Stands in for the pass itself: this module's job is the tool surface."""

    def __init__(self, *, port: object, store: object) -> None:
        self.port = port
        self.store = store

    def run(self, *, nip: str, token: str) -> SynchronisationReport:
        return SynchronisationReport(
            directions=(
                DirectionReport(
                    direction=InvoiceDirection.BUYER,
                    outcome=SyncOutcome.EXPORTED,
                    detail="Paczka EXP-1 gotowa do pobrania.",
                    invoice_count=7,
                    part_count=1,
                    reached=REACHED,
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
async def test_both_tools_are_registered(listed_tools: ListToolsResult) -> None:
    assert [tool.name for tool in listed_tools.tools] == [
        "server_info",
        "synchronise_invoices",
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
