"""The one place that defers loading the adapter, and the one place that says why.

`ksef2` pulls `lxml`, `signxml` and `xsdata` behind it, which costs about half
a second at import. A client listing tools pays that for nothing: only the
commands that actually reach KSeF touch the SDK, and `token status` may run in
a loop from a script.

So the import sits inside a function rather than at module scope — a deliberate
exception to the project's rule against inline imports, sanctioned in
`CLAUDE.md` for this reason and this reason only. The rule still holds for its
usual case, an import moved inside a function to dodge a circular dependency;
that is a fault in the declarations, not a cost worth paying at startup.

Gathered here because the rationale was written out at each call site, so
splitting `server.py` would have copied the same paragraph four times over and
left each copy free to drift (GH-126). One helper, one comment, and the call
sites say what they mean instead: load the adapter.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ksef_mcp.ksef_port.adapter import Ksef2Port


def load_adapter() -> "type[Ksef2Port]":
    from ksef_mcp.ksef_port.adapter import Ksef2Port

    return Ksef2Port
