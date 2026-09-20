import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from platformdirs import user_data_path

from ksef_mcp.errors import KsefMcpError
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.storage.durability import json_written_atomically, require_schema

CONFIGURATION_FILE: Final[str] = "configuration.json"

# The configuration was the last of six persistent documents to carry one, and
# the omission is what made a renamed key indistinguishable from a truncated
# file (GH-169). Version 1 is the shape four bare keys already had, so a file
# written before this build has no version and is refused by name rather than
# silently misread.
SCHEMA_VERSION: Final[int] = 1

INVOICE_DIRECTORY_MODE: Final[int] = 0o700

CONFIGURATION_FILE_MODE: Final[int] = 0o600

# Substrings of paths that a desktop sync client mirrors to somebody else's
# server. Invoice XML carries the counterparty's personal data, so a working
# directory inside one of these is a leak the user has to opt into knowingly.
CLOUD_SYNC_MARKERS: Final[tuple[str, ...]] = (
    "dropbox",
    "google drive",
    "googledrive",
    "gdrive",
    "onedrive",
    "icloud",
    "nextcloud",
    "pcloud",
    "mega",
    "yandex.disk",
)


# Never PRODUCTION: a default that reaches the live registry would make an
# accidental run a real filing, and production rate limits are a tenth of test.
DEFAULT_ENVIRONMENT: Final[KsefEnvironment] = KsefEnvironment.TEST


@dataclass(frozen=True)
class Configuration:
    nip: str
    environment: KsefEnvironment
    keyring_backend: str
    invoice_directory: Path


def configuration_path() -> Path:
    return user_data_path(appname=SERVER_NAME) / CONFIGURATION_FILE


class ConfigurationUnreadable(KsefMcpError):
    """The configuration file exists and this build cannot make sense of it.

    Its own type because the alternative was a `JSONDecodeError` or a `KeyError`
    leaving the server and the CLI both refusing to start, with no sentence
    anywhere saying that deleting one file is the way out (GH-168).
    """


def load_configuration(*, path: Path | None = None) -> Configuration | None:
    resolved = configuration_path() if path is None else path
    if not resolved.is_file():
        return None
    try:
        stored = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as damaged:
        raise ConfigurationUnreadable(
            f"{resolved} is not readable JSON — an interrupted write leaves the "
            f"file truncated. Run `ksef-mcp onboarding` to write it again."
        ) from damaged
    # A file written before versioning existed carries exactly these four keys,
    # so its absent version reads as 1 rather than turning every onboarding done
    # so far into a refusal. From 2 onward the key is there and the check bites.
    require_schema(
        {"schema_version": SCHEMA_VERSION} | stored,
        expected=SCHEMA_VERSION,
        named="Konfiguracja",
        refused_as=ConfigurationUnreadable,
        consequence=(
            "źle odczytany NIP albo środowisko zapisuje pliki pod niewłaściwym "
            "podmiotem albo trafia do żywego rejestru. Uruchom "
            "`ksef-mcp onboarding`, żeby zapisać ją od nowa."
        ),
    )
    try:
        return Configuration(
            nip=stored["nip"],
            environment=KsefEnvironment(stored["environment"]),
            keyring_backend=stored["keyring_backend"],
            invoice_directory=Path(stored["invoice_directory"]),
        )
    except (KeyError, TypeError, ValueError) as incomplete:
        raise ConfigurationUnreadable(
            f"{resolved} is missing or misstates {incomplete}. Run "
            f"`ksef-mcp onboarding` to write it again."
        ) from incomplete


def save_configuration(
    configuration: Configuration,
    *,
    path: Path | None = None,
) -> Path:
    """Staging file then rename, so an interrupted write never blocks the next start.

    This was the one persistence routine of seven writing straight into the
    target with `O_TRUNC`: a full disk or a lost session left a truncated file
    that stopped both the server and the CLI from starting, and nothing said so
    (GH-168). The staging file carries the final mode from creation, because the
    document names the taxpayer's NIP.
    """
    resolved = configuration_path() if path is None else path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": SCHEMA_VERSION,
        "nip": configuration.nip,
        "environment": str(configuration.environment),
        "keyring_backend": configuration.keyring_backend,
        "invoice_directory": str(configuration.invoice_directory),
    }
    return json_written_atomically(
        resolved,
        document=document,
        file_mode=CONFIGURATION_FILE_MODE,
    )


@dataclass(frozen=True)
class InvoiceDirectory:
    path: Path
    created: bool
    mode: int


def prepare_invoice_directory(directory: Path) -> InvoiceDirectory:
    existed = directory.is_dir()
    # Only a directory we created gets its permissions decided here. Someone
    # may point this at a home directory or a shared path, and silently
    # tightening what they already had is a change nobody asked for.
    directory.mkdir(mode=INVOICE_DIRECTORY_MODE, parents=True, exist_ok=True)
    return InvoiceDirectory(
        path=directory,
        created=not existed,
        mode=stat.S_IMODE(directory.stat().st_mode),
    )


def cloud_sync_marker(directory: Path) -> str | None:
    lowered = str(directory).lower()
    return next((marker for marker in CLOUD_SYNC_MARKERS if marker in lowered), None)
