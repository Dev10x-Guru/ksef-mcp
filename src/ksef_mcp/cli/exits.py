"""What the shell learns from a run. One number per outcome a script can act on."""

from typing import Final

EXIT_OK: Final[int] = 0

EXIT_UNUSABLE_KEYRING: Final[int] = 1

EXIT_NO_TOKEN: Final[int] = 2

EXIT_NOT_CONFIGURED: Final[int] = 3

EXIT_KSEF_REFUSED: Final[int] = 4

EXIT_SKILL_KEPT: Final[int] = 5

EXIT_PURGE_DECLINED: Final[int] = 6

EXIT_INVALID_WINDOW: Final[int] = 7

EXIT_INVALID_NIP: Final[int] = 8

EXIT_UNREADABLE_CONFIGURATION: Final[int] = 9
