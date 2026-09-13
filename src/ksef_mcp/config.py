import json
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from platformdirs import user_data_path

from ksef_mcp.metadata import SERVER_NAME

CONFIGURATION_FILE: Final[str] = "configuration.json"

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


class KsefEnvironment(StrEnum):
    TEST = "test"
    DEMO = "demo"
    PRODUCTION = "production"


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


def load_configuration(*, path: Path | None = None) -> Configuration | None:
    resolved = configuration_path() if path is None else path
    if not resolved.is_file():
        return None
    stored = json.loads(resolved.read_text(encoding="utf-8"))
    return Configuration(
        nip=stored["nip"],
        environment=KsefEnvironment(stored["environment"]),
        keyring_backend=stored["keyring_backend"],
        invoice_directory=Path(stored["invoice_directory"]),
    )


def save_configuration(
    configuration: Configuration,
    *,
    path: Path | None = None,
) -> Path:
    resolved = configuration_path() if path is None else path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "nip": configuration.nip,
        "environment": str(configuration.environment),
        "keyring_backend": configuration.keyring_backend,
        "invoice_directory": str(configuration.invoice_directory),
    }
    # Created with the final mode rather than written and then chmod-ed: the
    # gap between the two leaves the taxpayer's NIP readable to every local
    # account. umask can only clear bits, never add them, so this is safe.
    descriptor = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, CONFIGURATION_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    resolved.chmod(CONFIGURATION_FILE_MODE)
    return resolved


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
