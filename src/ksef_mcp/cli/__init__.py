"""The command line.

Re-exports rather than a rename: the console script declared in
`pyproject.toml` is `ksef_mcp.cli:main`, and it resolves to the same attribute
it resolved to when this package was one module (#134). `uvx ksef-mcp` is
untouched by the split.
"""

from ksef_mcp.cli.console import (
    AFFIRMATIVE_ANSWERS,
    Console,
    affirmative,
    ask_required,
    ask_secret_required,
    ask_with_default,
    default_console,
)
from ksef_mcp.cli.entrypoint import main
from ksef_mcp.cli.exits import (
    EXIT_INVALID_NIP,
    EXIT_INVALID_WINDOW,
    EXIT_KSEF_REFUSED,
    EXIT_NO_TOKEN,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
    EXIT_PURGE_DECLINED,
    EXIT_SKILL_KEPT,
    EXIT_UNREADABLE_CONFIGURATION,
    EXIT_UNUSABLE_KEYRING,
)

__all__ = [
    "AFFIRMATIVE_ANSWERS",
    "EXIT_INVALID_NIP",
    "EXIT_INVALID_WINDOW",
    "EXIT_KSEF_REFUSED",
    "EXIT_NOT_CONFIGURED",
    "EXIT_NO_TOKEN",
    "EXIT_OK",
    "EXIT_PURGE_DECLINED",
    "EXIT_SKILL_KEPT",
    "EXIT_UNREADABLE_CONFIGURATION",
    "EXIT_UNUSABLE_KEYRING",
    "Console",
    "affirmative",
    "ask_required",
    "ask_secret_required",
    "ask_with_default",
    "default_console",
    "main",
]
