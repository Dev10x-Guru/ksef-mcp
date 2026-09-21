"""The console script: read the command line, settle the context, answer once."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ksef_mcp import config
from ksef_mcp.cli.console import Console, default_console
from ksef_mcp.cli.context import CommandContext
from ksef_mcp.cli.exits import EXIT_UNREADABLE_CONFIGURATION, EXIT_UNUSABLE_KEYRING
from ksef_mcp.cli.parser import build_parser, dispatch
from ksef_mcp.diagnostics import technical_log
from ksef_mcp.storage import token_store


def main(
    argv: Sequence[str] | None = None,
    *,
    console: Console | None = None,
    working_directory: Path | None = None,
    configuration_file: Path | None = None,
    home: Path | None = None,
) -> int:
    reporting = default_console() if console is None else console
    context = CommandContext(
        console=reporting,
        working_directory=Path.cwd() if working_directory is None else working_directory,
        configuration_file=configuration_file,
        home=Path.home() if home is None else home,
    )
    try:
        return dispatch(build_parser().parse_args(argv), context=context)
    # These messages were written for exactly this moment; a traceback would
    # bury the one instruction that gets the person unstuck.
    except token_store.TokenStoreUnavailable as unavailable:
        reporting.write(str(unavailable))
        return EXIT_UNUSABLE_KEYRING
    # Every command that reads the configuration reaches this, rather than each
    # of the three call sites catching it for itself (GH-119). A damaged file
    # stops all of them, and the remedy is the same sentence for all of them.
    except config.ConfigurationUnreadable as damaged:
        technical_log().warning("Configuration unreadable: %s", damaged)
        reporting.write(str(damaged))
        return EXIT_UNREADABLE_CONFIGURATION
