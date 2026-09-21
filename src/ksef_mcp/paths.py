"""Which taxpayer we act as, and where that taxpayer's files live.

The glossary calls this *kontekst podmiotu*, and until now the project had no
type for it. A bare `nip: str` was doing two jobs at once — keying the keyring
entry and naming a directory — and the layout that turns one into the other was
spelled out in six stores that share nothing else (GH-113). Two consequences
followed from the same gap.

`123-456-32-18` and `1234563218` are one taxpayer to the tax office and were two
archives and two keyring entries here, silently (GH-111). `Nip.parsed` settles
the spelling once, at the boundary, so the number that reaches a path and the
number that reaches the keyring are the same number whichever way it was typed.

And an unparsed NIP reached `purge`, which deletes files (GH-112). `NIP_PATTERN`
is anchored and its alphabet is ten digits, so `..` and `/` cannot survive it —
the same reasoning `pdf.validated` applies to a KSeF number, applied to the
other caller-supplied string that becomes a path segment.

Normalisation happens where a spelling enters the process — the CLI, and the
server assembling one subject's dependencies — rather than inside
`load_configuration`. `config` reads a file and answers what this installation
was told to be; subject identity is a domain question, and an answer that has
to be read off disk before it can be checked is the wrong shape for one.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from platformdirs import user_cache_path, user_data_path

from ksef_mcp.errors import KsefMcpInputRejected
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME

SUBJECT_DIRECTORY: Final[str] = "subjects"

NIP_LENGTH: Final[int] = 10

# `\A`/`\Z` rather than `^`/`$`: `$` also matches before a trailing newline, and
# a path segment is exactly the place where that difference stops being academic.
NIP_PATTERN: Final[re.Pattern[str]] = re.compile(rf"\A[0-9]{{{NIP_LENGTH}}}\Z")

# The prefix a NIP travels with across a border. VIES prints it, Polish invoices
# to EU counterparties carry it, and it is not part of the number itself.
COUNTRY_PREFIX: Final[str] = "PL"

SEPARATORS: Final[str] = "-"


class NipRejected(KsefMcpInputRejected):
    """A spelling that is not a NIP.

    The rejected value is never repeated in the message. A taxpayer identifier
    is personal data and an exception travels into tracebacks, MCP error
    payloads and captured stderr (D-011).
    """


@dataclass(frozen=True)
class Nip:
    """A taxpayer's number in the one spelling this project stores it under."""

    value: str

    def __post_init__(self) -> None:
        if NIP_PATTERN.match(self.value) is None:
            raise NipRejected(
                f"A NIP is {NIP_LENGTH} digits and nothing else; got "
                f"{len(self.value)} characters. The value is not repeated here."
            )

    @classmethod
    def parsed(cls, spelling: str) -> Self:
        """Read a NIP the way a person writes one, store it the one way we store it."""
        condensed = "".join(
            character
            for character in spelling
            if not character.isspace() and character not in SEPARATORS
        )
        without_country = (
            condensed[len(COUNTRY_PREFIX) :]
            if condensed[: len(COUNTRY_PREFIX)].upper() == COUNTRY_PREFIX
            else condensed
        )
        return cls(without_country)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SubjectScope:
    """One taxpayer in one environment, and the directories that belong to them.

    Separate per subject and per environment for the reason every store used to
    restate on its own: a shared directory is the main way an accounting office
    mixes two clients (D-034), and a test continuation point reused against
    production would declare a period complete that was never fetched.

    The data root and the cache root are different roots on purpose (D-032). A
    disk cleaner may empty the cache one at any moment, so nothing whose loss
    costs more than one re-read is allowed to live there.
    """

    nip: Nip
    environment: KsefEnvironment

    @classmethod
    def parsed(cls, *, nip: str, environment: KsefEnvironment) -> Self:
        return cls(nip=Nip.parsed(nip), environment=environment)

    def data_root(self, *, override: Path | None = None) -> Path:
        base = user_data_path(appname=SERVER_NAME) if override is None else override
        return self.under(base)

    def cache_root(self, *, override: Path | None = None) -> Path:
        base = user_cache_path(appname=SERVER_NAME) if override is None else override
        return self.under(base)

    def under(self, base: Path) -> Path:
        return base / SUBJECT_DIRECTORY / self.nip.value / str(self.environment)


@dataclass(frozen=True)
class UnnormalisedSubject:
    """A subject directory named the way this version no longer names one."""

    directory: Path
    normalised: Path

    @property
    def normalised_exists(self) -> bool:
        return self.normalised.is_dir()


def subjects_root(*, override: Path | None = None) -> Path:
    base = user_data_path(appname=SERVER_NAME) if override is None else override
    return base / SUBJECT_DIRECTORY


def unnormalised_subjects(*, override: Path | None = None) -> tuple[UnnormalisedSubject, ...]:
    """Every subject directory written under a spelling we no longer write.

    Every subject, not the configured one: an accounting office onboards a
    client, changes the configuration to the next one, and the first client's
    directory is then reachable by nobody — the one shape of this problem the
    per-subject check could never see (GH-210).

    Nothing is moved here and nothing is moved by the caller either. An archive
    holds a counterparty's personal data, and relocating one behind the
    taxpayer's back is a worse answer than a directory they can see named and
    move themselves (GH-111).
    """
    holder = subjects_root(override=override)
    if not holder.is_dir():
        return ()
    return tuple(
        found
        for candidate in sorted(holder.iterdir())
        if (found := unnormalised(candidate, holder=holder)) is not None
    )


def unnormalised(candidate: Path, *, holder: Path) -> UnnormalisedSubject | None:
    if not candidate.is_dir():
        return None
    try:
        parsed = Nip.parsed(candidate.name)
    except NipRejected:
        return None
    if parsed.value == candidate.name:
        return None
    return UnnormalisedSubject(directory=candidate, normalised=holder / parsed.value)
