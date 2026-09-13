from mcp.server import MCPServer
from pydantic import BaseModel

from ksef_mcp.metadata import SERVER_NAME, VERSION

server: MCPServer = MCPServer(name=SERVER_NAME, version=VERSION)


class ServerInfo(BaseModel):
    name: str
    version: str


@server.tool()
def server_info() -> ServerInfo:
    """Report the name and version of the running KSeF connector."""
    return ServerInfo(name=SERVER_NAME, version=VERSION)


def main() -> None:
    server.run()
