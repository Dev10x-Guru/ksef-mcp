"""What a refusal may say out loud, and where the whole truth may be written.

D-038 settles a question GH-87 left open and GH-91 put: a KSeF number begins
with the NIP of the subject the invoice was issued for, so for the buyer and
the authorised-subject roles it is a counterparty's identifier rather than the
querying subject's. The refusal still has to say which manifest line it is
about, or it cannot be acted on at all.

So the number is split in two. `short_reference` derives a stable, opaque
handle from it — enough to tell two manifest lines apart and to recognise the
same line across two runs, carrying neither the NIP nor the date. That handle
is what a message shown to the MCP client carries. The full number goes to the
technical journal here, which reaches stderr and never the client.

The journal is deliberately not `AuditTrail`. That one is a record of data
access, append-only and complete by design; this one records failures and how
far a pass got.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Final

LOGGER_NAME: Final[str] = "ksef_mcp"

# Prefixed so a reader can tell at a glance that the thing in the message is a
# handle and not a shortened KSeF number they could paste into the portal.
REFERENCE_PREFIX: Final[str] = "ksef:"

# Twelve hex characters, so a package at the session ceiling of ten thousand
# invoices has a collision chance around one in ten million — far below the
# rate at which a manifest is malformed in the first place, which is the event
# these references appear in.
REFERENCE_LENGTH: Final[int] = 12


def short_reference(ksef_number: str) -> str:
    """The handle a client-facing refusal names an invoice by (D-038).

    A digest rather than a slice of the number: every slice long enough to
    distinguish manifest lines either starts at the NIP or ends in the
    two-character checksum, which distinguishes nothing.
    """
    digest = hashlib.sha256(ksef_number.encode("utf-8")).hexdigest()
    return f"{REFERENCE_PREFIX}{digest[:REFERENCE_LENGTH]}"


def technical_log() -> logging.Logger:
    """The journal. Never invoice content, never a token, never a full NIP."""
    return logging.getLogger(LOGGER_NAME)
