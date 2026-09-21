import pytest
from mcp import Client
from mcp.types import CallToolResult

from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.server import server


@pytest.fixture
async def server_info_result() -> CallToolResult:
    async with Client(server, raise_exceptions=True) as client:
        return await client.call_tool("server_info")


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
