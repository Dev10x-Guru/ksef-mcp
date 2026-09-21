"""Starting the stdio server, with the journal open before the first frame."""

import os

from ksef_mcp.diagnostics import configure_diagnostics, requested_log_directory
from ksef_mcp.server.app import server


def main() -> None:
    configure_diagnostics(directory=requested_log_directory(os.environ))
    server.run()
