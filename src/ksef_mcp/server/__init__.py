"""The MCP tool surface.

Re-exports rather than a rename: `from ksef_mcp.server import server` names the
same object it named when this package was one module, so splitting registration
from execution costs no caller anything (#133). Importing the `tools_*` modules
here is what registers the tools on `server` — the import of this package is the
moment the surface exists.
"""

from ksef_mcp.server.app import server
from ksef_mcp.server.entrypoint import main
from ksef_mcp.server.results import ServerInfo
from ksef_mcp.server.tools_identity import server_info
from ksef_mcp.server.tools_listing import list_recent_invoices
from ksef_mcp.server.tools_rendering import render_invoice_pdf
from ksef_mcp.server.tools_review import review_new_invoices
from ksef_mcp.server.tools_statement import export_period_statement
from ksef_mcp.server.tools_synchronisation import synchronise_invoices

__all__ = [
    "ServerInfo",
    "export_period_statement",
    "list_recent_invoices",
    "main",
    "render_invoice_pdf",
    "review_new_invoices",
    "server",
    "server_info",
    "synchronise_invoices",
]
