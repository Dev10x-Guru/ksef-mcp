import pytest
from mcp import Client
from mcp.types import CallToolResult, ListToolsResult

from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.server import main, server


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


@pytest.mark.anyio
async def test_server_info_is_registered(listed_tools: ListToolsResult) -> None:
    assert [tool.name for tool in listed_tools.tools] == ["server_info"]


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
