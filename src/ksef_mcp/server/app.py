"""The MCP server object every tool registers on, and the frame each call runs in.

Registration lives here; execution lives in the `tools_*` modules beside it. The
split is what keeps one file from being a quarter of the production code, and it
is why this module knows nothing about invoices, statements or renderers — it
knows how a call is framed, journalled and refused, and nothing about what the
call was for.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ksef_mcp.diagnostics import correlated, technical_log
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.server.errors import REFUSALS, AuditNotRecorded, NotConfigured
from ksef_mcp.storage.audit import AuditedOperation, AuditEntry, AuditTrail

server: MCPServer = MCPServer(name=SERVER_NAME, version=VERSION)


@dataclass
class Journal:
    """The audit write, handed to a tool so that not making it is an event.

    A tool used to reach `AuditTrail.record` on its own, and nothing anywhere
    noticed when a new one did not. Passing the write through an object the
    frame keeps a reference to turns the omission from a silent gap in the
    evidence into a refusal the caller reads (#136) — the same frame that
    already wraps every tool, so there is no second layer to remember either.
    """

    operation: AuditedOperation
    written: bool = False

    def record(self, *, trail: AuditTrail, entries: Iterable[AuditEntry]) -> Path:
        self.written = True
        return trail.record(entries)


@contextmanager
def reported(operation: AuditedOperation) -> Iterator[Journal]:
    """Name the failure to the caller instead of letting the SDK swallow it.

    An exception the SDK does not recognise as anticipated reaches the client as
    a bare `Error executing tool <name>` and its text stays in a stderr the
    caller cannot see — which is how a schema mismatch in the limits response
    looked like a dead server (GH-76). `ToolError` is the SDK's channel for a
    failure we saw coming, so the reason travels with it.

    The refusals a tool raises about its own arguments belong here as much as
    the port's do (GH-84). Catching only the port left `render_invoice_pdf`
    answering "Error executing tool" for an invoice that was simply not
    synchronised yet and for a working directory it had declined — both
    outcomes its own docstring promises to explain.

    Enumerating those refusals one by one had the same failure a second time,
    and on the commonest path of all: a keyring collection locks itself when the
    machine suspends, four of the five tools read a token, and none of them
    reached `LOCKED_MESSAGE` (GH-167). The tuple is now two roots, so an
    exception written for a reader arrives without anyone remembering to list
    it.

    The refusal is journalled too (GH-116): a client that saw the sentence
    still leaves nothing an operator can reconstruct the pass from, and the
    sentence itself is gone as soon as the conversation moves on.

    This is also where a tool call acquires its identity (GH-117). Everything
    one call does to KSeF happens inside this block, so minting here and
    resetting on the way out is what lets the journal be read back as one
    sequence instead of eight rows sharing a `recorded_at`.

    The audit write is checked on the way out for the same reason (#136). A
    tool that answered without recording the access leaves the one record a
    leak dispute turns on missing, and `AuditNotRecorded` is a `KsefMcpError`,
    so it leaves through the refusal branch below with its sentence intact
    rather than as a crash nobody can read.
    """
    journal = Journal(operation=operation)
    with correlated():
        try:
            yield journal
            if not journal.written:
                raise AuditNotRecorded(
                    f"{operation} odpowiedziało bez wpisu do dziennika audytu. "
                    f"Każde narzędzie zapisuje dostęp przez `Journal.record`."
                )
        except NotConfigured as error:
            technical_log().warning("%s refused: not configured. %s", operation, error)
            raise ToolError(
                f"{operation} wymaga konfiguracji. Uruchom `ksef-mcp onboarding`. ({error})"
            ) from error
        except REFUSALS as error:
            # The message is safe to repeat here for the same reason it is safe
            # to send to the client: `KsefMcpError` promises it carries no
            # token, no invoice body and, since D-038, no KSeF number in full.
            technical_log().warning("%s refused: %s", operation, error)
            raise ToolError(f"{operation} nie zakończyło się: {error}") from error
