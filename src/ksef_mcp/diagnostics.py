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
technical journal here, which reaches stderr and an optional file the operator
opted into, and never the client and never the model's context.

The journal is deliberately not `AuditTrail`. That one is a record of data
access, append-only and complete by design; this one records failures and how
far a pass got, so that a synchronisation that did not finish can be
reconstructed without asking for it again out of twenty exports an hour
(GH-116).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from platformdirs import user_data_path

from ksef_mcp.metadata import SERVER_NAME

LOGGER_NAME: Final[str] = "ksef_mcp"

# Prefixed so a reader can tell at a glance that the thing in the message is a
# handle and not a shortened KSeF number they could paste into the portal.
REFERENCE_PREFIX: Final[str] = "ksef:"

# Twelve hex characters, so a package at the session ceiling of ten thousand
# invoices has a collision chance around one in ten million — far below the
# rate at which a manifest is malformed in the first place, which is the event
# these references appear in.
REFERENCE_LENGTH: Final[int] = 12

LOG_FILE: Final[str] = "diagnostics.log"

# Same mode as every other file this project writes under the data root. The
# journal carries KSeF numbers in full, so it is exactly as sensitive as the
# archive it describes.
LOG_FILE_MODE: Final[int] = 0o600

LOG_DIRECTORY_MODE: Final[int] = 0o700

LOG_FILE_BYTES: Final[int] = 1_048_576

LOG_FILE_KEPT: Final[int] = 3

# Opt-in and off by default. A server that writes a file nobody asked for
# leaves KSeF numbers on a disk whose backup policy nobody considered.
LOG_DIRECTORY_VARIABLE: Final[str] = "KSEF_DIAGNOSTIC_DIRECTORY"

LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(message)s"


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


def requested_log_directory(environment: Mapping[str, str]) -> Path | None:
    """Where the operator asked for a file journal, or nowhere.

    The default data root is offered by the bare value `1`, so enabling the
    journal does not require the person to know where `platformdirs` puts
    things on their system.
    """
    stated = environment.get(LOG_DIRECTORY_VARIABLE, "").strip()
    if stated in ("", "0"):
        return None
    if stated == "1":
        return user_data_path(appname=SERVER_NAME)
    return Path(stated).expanduser()


def _stream_handler() -> logging.Handler:
    # stderr, never stdout: stdout is the stdio transport the MCP client reads
    # frames from, and one log line there ends the session.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def _file_handler(directory: Path) -> logging.Handler:
    directory.mkdir(mode=LOG_DIRECTORY_MODE, parents=True, exist_ok=True)
    path = directory / LOG_FILE
    # Created with its final mode rather than written and then tightened: the
    # window between the two is when another account reads it.
    path.touch(mode=LOG_FILE_MODE, exist_ok=True)
    path.chmod(LOG_FILE_MODE)
    handler = RotatingFileHandler(
        path,
        maxBytes=LOG_FILE_BYTES,
        backupCount=LOG_FILE_KEPT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def configure_diagnostics(*, directory: Path | None = None) -> logging.Logger:
    """Point the journal at stderr, and at a file when one was asked for."""
    log = technical_log()
    for stale in tuple(log.handlers):
        log.removeHandler(stale)
    log.setLevel(logging.INFO)
    # Never up to the root logger: a client application embedding this package
    # configures its own root, and the journal would then reach handlers that
    # may well write to stdout.
    log.propagate = False
    log.addHandler(_stream_handler())
    if directory is not None:
        log.addHandler(_file_handler(directory))
    return log
