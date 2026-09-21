"""What the tool surface refuses with, and what it refuses to let pass silently."""

from typing import Final

from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.errors import KsefPortError


class NotConfigured(KsefMcpError):
    pass


class AuditNotRecorded(KsefMcpError):
    """A tool answered without asking the journal to write the access down.

    Raised at the end of `reported()` rather than documented in a checklist: the
    value of the trail is its completeness (D-011), and a tool that fetches data
    and forgets the entry passes every other test there is. The name of the
    operation is the whole message — the answer it would have returned holds
    KSeF numbers, and those never belong in an exception.
    """


# Refusals whose text was written for the person reading it: each names
# something the caller can act on — KSeF declining the call, a number not in the
# archive, a directory the server will not write to, a missing Node, a month it
# could not parse, a keyring collection that locked itself while the laptop
# slept — and none carries the NIP, the token or a line of invoice XML (D-011).
#
# Two roots rather than a hand-kept list of names. The list was the earlier
# design and its argument was that membership should cost a reviewer's glance;
# what it actually cost was three of five tools answering `Error executing tool`
# with the explaining sentence already written and thrown away (GH-167). The
# promise did not disappear — it moved to `KsefMcpError`, where the person
# writing the exception makes it, instead of to a tuple in another module that
# nobody was reminded to visit.
REFUSALS: Final[tuple[type[Exception], ...]] = (KsefPortError, KsefMcpError)
