import json
import stat
from pathlib import Path

import pytest

from ksef_mcp import config
from ksef_mcp.config import Configuration, KsefEnvironment


@pytest.fixture
def configuration(tmp_path: Path) -> Configuration:
    return Configuration(
        nip="1234567890",
        environment=KsefEnvironment.TEST,
        keyring_backend="keyring.backends.SecretService",
        invoice_directory=tmp_path / "faktury",
    )


@pytest.fixture
def configuration_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / config.CONFIGURATION_FILE


@pytest.fixture
def saved_configuration(configuration: Configuration, configuration_file: Path) -> Path:
    return config.save_configuration(configuration, path=configuration_file)


def test_default_environment_never_reaches_production() -> None:
    assert config.DEFAULT_ENVIRONMENT is KsefEnvironment.TEST


def test_configuration_path_sits_under_the_server_name() -> None:
    assert config.configuration_path().name == config.CONFIGURATION_FILE


def test_missing_configuration_reads_as_absent(configuration_file: Path) -> None:
    assert config.load_configuration(path=configuration_file) is None


def test_saved_configuration_round_trips(
    saved_configuration: Path,
    configuration: Configuration,
) -> None:
    assert config.load_configuration(path=saved_configuration) == configuration


def test_saved_configuration_is_readable_json(saved_configuration: Path) -> None:
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))

    assert stored["environment"] == "test"


def test_saved_configuration_is_not_world_readable(saved_configuration: Path) -> None:
    assert stat.S_IMODE(saved_configuration.stat().st_mode) == config.CONFIGURATION_FILE_MODE


def test_invoice_directory_is_created_private(tmp_path: Path) -> None:
    prepared = config.prepare_invoice_directory(tmp_path / "faktury" / "1234567890")

    assert stat.S_IMODE(prepared.path.stat().st_mode) == config.INVOICE_DIRECTORY_MODE


def test_a_created_invoice_directory_says_so(tmp_path: Path) -> None:
    assert config.prepare_invoice_directory(tmp_path / "faktury").created is True


def test_an_existing_invoice_directory_keeps_its_permissions(tmp_path: Path) -> None:
    shared = tmp_path / "wspolny"
    shared.mkdir(mode=0o755)

    prepared = config.prepare_invoice_directory(shared)

    assert (prepared.created, prepared.mode) == (False, 0o755)


def test_invoice_directory_preparation_is_repeatable(tmp_path: Path) -> None:
    target = tmp_path / "faktury"
    config.prepare_invoice_directory(target)

    assert config.prepare_invoice_directory(target).path.is_dir()


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        (Path("/home/ksiegowa/Dropbox/faktury"), "dropbox"),
        (Path("/Users/ksiegowa/Google Drive/ksef"), "google drive"),
        (Path("/home/ksiegowa/OneDrive/ksef"), "onedrive"),
        (Path("/home/ksiegowa/ksef/faktury"), None),
    ],
)
def test_cloud_sync_marker(directory: Path, expected: str | None) -> None:
    assert config.cloud_sync_marker(directory) == expected
