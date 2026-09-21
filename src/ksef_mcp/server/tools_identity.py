"""Which build answered. The one tool that reads nothing belonging to a taxpayer."""

from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.server.app import server
from ksef_mcp.server.results import ServerInfo


@server.tool()
def server_info() -> ServerInfo:
    """Report the name and version of the running KSeF connector."""
    return ServerInfo(name=SERVER_NAME, version=VERSION)
