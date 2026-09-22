import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from ksef_mcp import config
from ksef_mcp.config import Configuration
from ksef_mcp.ksef_port.types import KsefEnvironment


@pytest.fixture
def configuration(tmp_path: Path) -> Configuration:
    return Configuration(
        nip="1234567890",
        environment=KsefEnvironment.TEST,
        keyring_backend="keyring.backends.SecretService",
        working_directory=tmp_path / "zestawienia",
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


def test_the_working_directory_keeps_the_key_written_so_far(
    saved_configuration: Path,
    configuration: Configuration,
) -> None:
    # GH-188 przemianowało pole, nie klucz: plik zapisany wcześniej ma zostać
    # czytelny bez schematu 2 i bez ponownego onboardingu.
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))

    assert stored["invoice_directory"] == str(configuration.working_directory)


@pytest.fixture
def acknowledged(configuration: Configuration) -> Configuration:
    return configuration.acknowledging(Path("/home/ksiegowa/Dropbox/ksef"))


def test_an_acknowledged_directory_round_trips(
    acknowledged: Configuration, configuration_file: Path
) -> None:
    saved = config.save_configuration(acknowledged, path=configuration_file)

    assert config.load_configuration(path=saved) == acknowledged


def test_a_configuration_written_before_acknowledgements_reads_with_none(
    saved_configuration: Path,
) -> None:
    # GH-252 added two optional keys; a file from before carries neither and
    # must still read as schema 1 rather than send anybody through onboarding.
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))
    del stored[config.ACKNOWLEDGED_CLOUD_DIRECTORIES_KEY]
    del stored[config.CLOUD_WARNINGS_KEY]
    saved_configuration.write_text(json.dumps(stored), encoding="utf-8")

    loaded = config.load_configuration(path=saved_configuration)

    assert (loaded.acknowledged_cloud_directories, loaded.cloud_warnings) == ((), True)


def test_an_acknowledged_directory_is_not_warned_about_again(
    acknowledged: Configuration,
) -> None:
    assert acknowledged.cloud_marker_for(Path("/home/ksiegowa/Dropbox/ksef")) is None


def test_the_same_directory_reached_another_way_counts_as_acknowledged(
    acknowledged: Configuration,
) -> None:
    # Compared resolved, as the internal-root check is: a `..` on the way in
    # is still the directory the taxpayer said yes to.
    roundabout = Path("/home/ksiegowa/Dropbox/inne/../ksef")

    assert acknowledged.cloud_marker_for(roundabout) is None


def test_another_synced_directory_is_still_warned_about(acknowledged: Configuration) -> None:
    assert acknowledged.cloud_marker_for(Path("/home/ksiegowa/OneDrive/ksef")) == "onedrive"


def test_the_switch_silences_every_synced_directory(configuration: Configuration) -> None:
    silenced = replace(configuration, cloud_warnings=False)

    assert silenced.cloud_marker_for(Path("/home/ksiegowa/OneDrive/ksef")) is None


def test_acknowledging_the_same_directory_twice_records_it_once(
    acknowledged: Configuration,
) -> None:
    again = acknowledged.acknowledging(Path("/home/ksiegowa/Dropbox/ksef"))

    assert again.acknowledged_cloud_directories == (Path("/home/ksiegowa/Dropbox/ksef"),)


def test_saved_configuration_is_not_world_readable(saved_configuration: Path) -> None:
    assert stat.S_IMODE(saved_configuration.stat().st_mode) == config.CONFIGURATION_FILE_MODE


def test_a_saved_configuration_states_the_schema_it_was_written_in(
    saved_configuration: Path,
) -> None:
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))

    assert stored["schema_version"] == config.SCHEMA_VERSION


def test_a_configuration_from_a_later_build_is_refused_by_name(
    saved_configuration: Path,
) -> None:
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))
    saved_configuration.write_text(
        json.dumps(stored | {"schema_version": config.SCHEMA_VERSION + 1}),
        encoding="utf-8",
    )

    with pytest.raises(config.ConfigurationUnreadable, match="ksef-mcp onboarding"):
        config.load_configuration(path=saved_configuration)


def test_a_configuration_written_before_versioning_still_reads(
    saved_configuration: Path,
    configuration: Configuration,
) -> None:
    # The four keys are unchanged, so an absent version is this version — the
    # alternative would refuse every onboarding done before GH-169.
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))
    del stored["schema_version"]
    saved_configuration.write_text(json.dumps(stored), encoding="utf-8")

    assert config.load_configuration(path=saved_configuration) == configuration


def test_a_truncated_configuration_reads_as_a_named_refusal(
    saved_configuration: Path,
) -> None:
    """GH-168: an interrupted write stopped both the server and the CLI starting.

    A file NOT PRESENT and a file CUT IN HALF are two different things, and only
    the first one had a test.
    """
    whole = saved_configuration.read_text(encoding="utf-8")
    saved_configuration.write_text(whole[: len(whole) // 2], encoding="utf-8")

    with pytest.raises(config.ConfigurationUnreadable, match="ksef-mcp onboarding"):
        config.load_configuration(path=saved_configuration)


def test_a_configuration_missing_a_key_is_refused_rather_than_indexed(
    saved_configuration: Path,
) -> None:
    stored = json.loads(saved_configuration.read_text(encoding="utf-8"))
    del stored["nip"]
    saved_configuration.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(config.ConfigurationUnreadable, match="ksef-mcp onboarding"):
        config.load_configuration(path=saved_configuration)


def test_an_interrupted_write_leaves_the_previous_configuration_intact(
    saved_configuration: Path,
    configuration: Configuration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the staging file: the target is replaced or it is untouched."""

    def full_disk(*arguments: object, **keywords: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(config, "json_written_atomically", full_disk)

    with pytest.raises(OSError, match="No space left"):
        config.save_configuration(
            Configuration(
                nip="9876543210",
                environment=KsefEnvironment.DEMO,
                keyring_backend="keyring.backends.fail",
                working_directory=saved_configuration.parent,
            ),
            path=saved_configuration,
        )

    assert config.load_configuration(path=saved_configuration) == configuration


def test_a_saved_configuration_leaves_no_staging_file_behind(
    saved_configuration: Path,
) -> None:
    assert sorted(one.name for one in saved_configuration.parent.iterdir()) == [
        config.CONFIGURATION_FILE
    ]


def test_a_prepared_directory_is_created_private(tmp_path: Path) -> None:
    prepared = config.prepare_directory(tmp_path / "zestawienia" / "1234567890")

    assert stat.S_IMODE(prepared.path.stat().st_mode) == config.WORKING_DIRECTORY_MODE


def test_a_created_directory_says_so(tmp_path: Path) -> None:
    assert config.prepare_directory(tmp_path / "zestawienia").created is True


def test_an_existing_directory_keeps_its_permissions(tmp_path: Path) -> None:
    shared = tmp_path / "wspolny"
    shared.mkdir(mode=0o755)

    prepared = config.prepare_directory(shared)

    assert (prepared.created, prepared.mode) == (False, 0o755)


def test_directory_preparation_is_repeatable(tmp_path: Path) -> None:
    target = tmp_path / "zestawienia"
    config.prepare_directory(target)

    assert config.prepare_directory(target).path.is_dir()


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
