"""What a command needs that the command line does not carry."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ksef_mcp.cli.console import Console


@dataclass(frozen=True)
class CommandContext:
    """The terminal and the three paths, settled once by `main`.

    Separate from the parsed arguments because none of it is typed on the
    command line: a test hands a directory and a configuration file straight
    in, and every command then reads the same ones.
    """

    console: Console
    working_directory: Path
    configuration_file: Path | None
    home: Path


CommandHandler = Callable[[argparse.Namespace, CommandContext], int]
